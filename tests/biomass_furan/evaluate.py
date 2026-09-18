"""Real HTTP or isolated Pipeline benchmark; no answer generation in retrieval runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import platform
import random
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(PROJECT / 'src'))
from freeze import digest, frozen_rows, write_json

KS = (1, 3, 5, 10, 20)


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def read_qrels(path):
    labels = defaultdict(dict)
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    if not lines or lines[0] != 'query_id\tdoc_id\trelevance':
        raise ValueError('Invalid qrels header')
    for line in lines[1:]:
        qid, did, rel = line.split('\t')
        rel = int(rel)
        if rel not in range(4) or did in labels[qid]:
            raise ValueError('Invalid/duplicate qrel')
        labels[qid][did] = rel
    return dict(labels)


def score_query(ids, qrels, *, answerable=True, allow_unjudged=False):
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate returned doc_id; do not silently inflate or compress ranks')
    if not allow_unjudged and any(did not in qrels for did in ids):
        raise ValueError('Unjudged returned document')
    if not answerable:
        return {'no_answer_nonempty': float(bool(ids)), 'returned_count': len(ids)}
    relevant = {d for d, r in qrels.items() if r >= 2}
    if not relevant:
        raise ValueError('Answerable query without positive evidence')
    metrics = {}
    for k in KS:
        hits = len(set(ids[:k]) & relevant)
        metrics[f'Recall@{k}'] = hits / len(relevant)
        metrics[f'Precision@{k}'] = hits / k
        metrics[f'HitRate@{k}'] = float(hits > 0)
        dcg = sum((2 ** qrels.get(did, 0) - 1) / math.log2(i + 2)
                  for i, did in enumerate(ids[:k]))
        ideal = sum((2 ** rel - 1) / math.log2(i + 2)
                    for i, rel in enumerate(sorted(qrels.values(), reverse=True)[:k]))
        metrics[f'nDCG@{k}'] = dcg / ideal if ideal else None
    metrics['MRR@10'] = next((1 / i for i, did in enumerate(ids[:10], 1) if did in relevant), 0.0)
    return metrics


def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * p / 100
    lo = math.floor(position)
    hi = math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def latency_stats(values):
    return {'count': len(values), 'mean_ms': statistics.mean(values) if values else None,
            **{f'p{p}_ms': percentile(values, p) for p in (50, 95, 99)}}


def macro(items):
    return {k: statistics.mean(row[k] for row in items if row.get(k) is not None)
            for k in sorted({k for row in items for k in row})
            if any(row.get(k) is not None for row in items)}


def http_json(url, payload=None, timeout=120):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=body,
                                    headers={'Content-Type': 'application/json'} if body else {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def append_json(path, value):
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(value, ensure_ascii=False) + '\n')


def assert_frozen_source():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    with sqlite3.connect((PROJECT / 'var' / 'rag.sqlite3').as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        current = {r['chunk_id']: hashlib.sha256(r['text'].encode()).hexdigest()
                   for r in frozen_rows(conn, datetime.now(timezone.utc).isoformat())}
    frozen = {d['doc_id']: d['metadata']['text_sha256'] for d in read_jsonl(ROOT / 'corpus.jsonl')}
    if current != frozen or digest(ROOT / 'corpus.jsonl') != manifest['corpus_sha256']:
        raise ValueError('Corpus drift: current production scope is not the frozen benchmark')


def summarize(run_dir, *, provisional=False, qrels_path=None):
    queries = read_jsonl(ROOT / 'queries.jsonl')
    corpus_ids = {d['doc_id'] for d in read_jsonl(ROOT / 'corpus.jsonl')}
    runs = read_jsonl(run_dir / 'runs.jsonl') if (run_dir / 'runs.jsonl').exists() else []
    formal = [r for r in runs if r['phase'] == 'formal']
    labels = read_qrels(qrels_path or (ROOT / 'qrels.tsv')) if (qrels_path or (ROOT / 'qrels.tsv')).exists() else {}
    complete = len(labels) == len(queries) and all(set(labels.get(q['query_id'], {})) == corpus_ids for q in queries)
    results, scored = [], []
    for q in queries:
        records = [r for r in formal if r['query_id'] == q['query_id']]
        if not records:
            continue
        # Quality comes from repeat 1, not whichever request happened to be fastest.
        first = min(records, key=lambda r: r['repeat'])
        ids = [item['doc_id'] for item in first['results']]
        unknown_ids = set(ids) - corpus_ids
        if unknown_ids:
            raise ValueError('Returned doc IDs outside frozen corpus')
        row = {'query_id': q['query_id'], 'repeat_count': len(records),
               'latency_median_ms': statistics.median(r['latency_ms'] for r in records),
               'quality_run_repeat': first['repeat'], 'status': first['status'],
               'degraded': first.get('degraded', False),
               'ranking_stable': all([x['doc_id'] for x in r['results']] == ids for r in records),
               'judged_at_20': sum(d in labels.get(q['query_id'], {}) for d in ids) / len(ids) if ids else 1.0}
        if complete or provisional:
            row['metrics'] = score_query(ids, labels.get(q['query_id'], {}),
                                         answerable=q['answerable'], allow_unjudged=provisional)
            scored.append((q, row))
        results.append(row)
    groups = {}
    for field in ('split', 'question_type', 'difficulty', 'language'):
        groups[field] = {value: macro([r['metrics'] for q, r in scored if q['answerable'] and q[field] == value])
                         for value in sorted({q[field] for q, _ in scored})}
    positive_scores = [(q, r) for q, r in scored if q['answerable']]
    meta = json.loads((run_dir / 'run_config.json').read_text(encoding='utf-8'))
    server_values = [r['server_retrieve_ms'] for r in formal if isinstance(r.get('server_retrieve_ms'), (int, float))]
    final = {'dataset_version': '0.1.0-ai-candidate', 'qrels_complete': complete,
             'metric_status': 'complete_qrels_AI_only' if complete else ('PROVISIONAL_UNJUDGED_AS_ZERO' if provisional else 'blocked_incomplete_qrels'),
             'run_complete': len(formal) == len(queries) * 3 and all(r['repeat_count'] == 3 for r in results),
             'stable_hybrid_baseline': len(formal) == len(queries)*3 and all(r['status']=='ok' and not r.get('degraded',False) for r in formal),
             'successful_formal_requests': sum(r['status']=='ok' for r in formal),
             'config': meta, 'formal_requests': len(formal),
             'status_counts': {s: sum(r['status'] == s for r in formal) for s in sorted({r['status'] for r in formal})},
             'timeout_rate': sum(r['status'] == 'timeout' for r in formal) / len(formal) if formal else None,
             'degradation_rate': sum(r.get('degraded', False) for r in formal) / len(formal) if formal else None,
             'latency_query_medians': latency_stats([r['latency_median_ms'] for r in results]),
             'latency_all_requests': latency_stats([r['latency_ms'] for r in formal]),
             'successful_request_latency_diagnostic': latency_stats([r['latency_ms'] for r in formal if r['status']=='ok']),
             'reranker_latency_diagnostic': latency_stats([r['reranker_ms'] for r in formal if 'reranker_ms' in r]),
             'non_rerank_estimate_latency_diagnostic': latency_stats([r['non_rerank_estimate_ms'] for r in formal if 'non_rerank_estimate_ms' in r]),
             'server_internal_retrieve_latency_diagnostic': latency_stats(server_values),
             'overall': macro([r['metrics'] for q, r in positive_scores]),
             'normal_hybrid_only': macro([r['metrics'] for q, r in positive_scores if r['status'] == 'ok' and not r['degraded']]),
             'groups': groups,
             'no_answer': [{'query_id': q['query_id'], 'nonempty_rate': statistics.mean(bool(r['results']) for r in formal if r['query_id'] == q['query_id']),
                            'returned_counts': [len(r['results']) for r in formal if r['query_id'] == q['query_id']]}
                           for q in queries if not q['answerable'] and any(r['query_id'] == q['query_id'] for r in formal)],
             'per_query': results}
    write_json(run_dir / ('summary_provisional.json' if provisional else 'summary.json'), final)
    return final


def run_http(args):
    if args.timeout != 120:
        raise ValueError('This frozen benchmark uses a 120-second request ceiling')
    assert_frozen_source()
    run_dir = ROOT / 'results' / datetime.now(timezone.utc).strftime('http-%Y%m%dT%H%M%S%fZ')
    run_dir.mkdir(parents=True)
    ready = http_json(args.base_url.rstrip('/') + '/ready', timeout=10)
    safe_ready = {k: v for k, v in ready.items() if k in {'ready','chat_configured','embedding_configured','embedding_index','reranker_provider','schema_version','paper_count','chunk_count'}}
    config = {'mode': 'production_http', 'base_url': args.base_url, 'top_k': 20,
        'warmup': 20, 'repeats': 3, 'concurrency': 1, 'timeout_seconds': 120, 'seed': 20260916,
        'hardware': {'platform': platform.platform(), 'cpu': platform.processor(), 'logical_cpu_count': os.cpu_count()},
        'python': platform.python_version(), 'ready': safe_ready,
        'corpus_sha256': digest(ROOT / 'corpus.jsonl'), 'queries_sha256': digest(ROOT / 'queries.jsonl'),
        'latency_boundary': 'HTTP client request to fully parsed response; includes local transport and serialization',
        'server_timing_limit': 'Existing timings.retrieve excludes analysis and some response bookkeeping; diagnostic only.',
        'production_query_logs_authorized': True,
        'cold_start': 'not_measured_existing_running_service',
        'cache_policy': 'existing service; warmed, no cache flush',
        'quality_repeat': 1}
    write_json(run_dir / 'run_config.json', config)
    queries = read_jsonl(ROOT / 'queries.jsonl')
    schedule = [('warmup', n + 1, queries[n % len(queries)]) for n in range(20)]
    rng = random.Random(20260916)
    for repeat in range(1, 4):
        ordered = list(queries)
        rng.shuffle(ordered)
        schedule.extend(('formal', repeat, q) for q in ordered)
    ids = {d['doc_id'] for d in read_jsonl(ROOT / 'corpus.jsonl')}
    print(str(run_dir), flush=True)
    for index, (phase, repeat, q) in enumerate(schedule):
        before = time.perf_counter()
        row = {'phase': phase, 'repeat': repeat, 'query_id': q['query_id'], 'results': [],
               'status': 'ok', 'degraded': False, 'started_at': datetime.now(timezone.utc).isoformat()}
        try:
            response = http_json(args.base_url.rstrip('/') + '/v1/retrieve',
                {'question': q['question'], 'top_k': 20, 'include_official': True}, timeout=args.timeout)
            row['latency_ms'] = (time.perf_counter() - before) * 1000
            row['request_id'] = response.get('request_id')
            row['degraded'] = response.get('degraded', False)
            row['degradation_reason'] = response.get('degradation_reason')
            row['server_retrieve_ms'] = response.get('timings', {}).get('retrieve')
            seen = set()
            for rank, item in enumerate(response.get('results', []), 1):
                did = item['chunk_id']
                if did not in ids or did in seen or not math.isfinite(float(item['score'])) or rank > 20:
                    raise ValueError('Invalid retrieved results')
                seen.add(did)
                row['results'].append({'rank': rank, 'doc_id': did, 'score': item['score']})
        except (TimeoutError, urllib.error.URLError) as exc:
            timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError)
            row.update(status='timeout' if timed_out else 'error', results=[], error_type=type(exc).__name__)
            row['latency_ms'] = args.timeout * 1000 if timed_out else (time.perf_counter() - before) * 1000
        except (ValueError, KeyError, urllib.error.HTTPError) as exc:
            row.update(status='error', results=[], error_type=type(exc).__name__)
            row['latency_ms'] = (time.perf_counter() - before) * 1000
        append_json(run_dir / 'runs.jsonl', row)
        if (index + 1) % 10 == 0:
            print(json.dumps({'completed': index + 1, 'of': len(schedule), 'last_status': row['status'],
                              'last_latency_ms': round(row['latency_ms'])}), flush=True)
    assert_frozen_source()
    summary = summarize(run_dir)
    print(json.dumps({k: summary[k] for k in ('formal_requests', 'run_complete', 'metric_status', 'latency_query_medians')}), flush=True)


def isolated_worker(pipe, settings):
    """Own process provides a hard timeout boundary without leaving timed-out threads running."""
    from rag.pipeline import Pipeline
    import rag.pipeline as module
    from unittest.mock import patch
    pipeline = Pipeline(settings)
    factory = module.configured_reranker
    rerank_ms = [0.0]

    class TimedReranker:
        def __init__(self, inner):
            self.inner = inner

        def rerank(self, *args, **kwargs):
            began = time.perf_counter()
            try:
                return self.inner.rerank(*args, **kwargs)
            finally:
                rerank_ms[0] += (time.perf_counter() - began) * 1000

    def timed_factory(provider):
        inner = factory(provider)
        return TimedReranker(inner) if inner is not None else None

    actual = pipeline.store.scoped_chunks('default', 'anonymous', include_official=True)
    expected = {d['doc_id']: d['metadata']['text_sha256'] for d in read_jsonl(ROOT / 'corpus.jsonl')}
    if {d['chunk_id']: hashlib.sha256(d['text'].encode()).hexdigest() for d in actual} != expected:
        pipe.send({'error': 'isolated_scope_drift'})
        return
    pipe.send({'ready': True})
    while True:
        question = pipe.recv()
        if question is None:
            return
        rerank_ms[0] = 0.0
        began = time.perf_counter()
        try:
            with patch.object(module, 'configured_reranker', timed_factory):
                result = pipeline.retrieve(question, top_k=20, tenant_id='default', subject='anonymous', include_official=True)
            elapsed = (time.perf_counter() - began) * 1000
            pipe.send({'response': result, 'latency_ms': elapsed, 'reranker_ms': rerank_ms[0],
                       'non_rerank_estimate_ms': max(0, elapsed-rerank_ms[0])})
        except Exception as exc:
            pipe.send({'error': type(exc).__name__, 'latency_ms': (time.perf_counter()-began)*1000})


def run_isolated(args, settings_override=None):
    from dataclasses import replace
    from rag.config import Settings
    settings = settings_override or Settings.load()
    if not args.offline_bm25 and not settings.embedding_api_key:
        raise ValueError('Missing embedding credentials; use secure_run.py or --offline-bm25 explicitly')
    run_dir = ROOT / 'results' / datetime.now(timezone.utc).strftime('isolated-%Y%m%dT%H%M%S%fZ')
    runtime = ROOT / 'runtime' / run_dir.name
    runtime.mkdir(parents=True)
    run_dir.mkdir(parents=True)
    with sqlite3.connect((ROOT / 'snapshot/corpus.sqlite3').as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(runtime / 'rag.sqlite3') as dst:
            src.backup(dst)
    settings = replace(settings, db_path=runtime/'rag.sqlite3', var_dir=runtime,
        log_path=runtime/'logs/rag.jsonl', chat_provider='extractive', chat_api_key=None,
        ocr_enabled=False, embedding_timeout_seconds=120,
        embedding_provider='disabled' if args.offline_bm25 else settings.embedding_provider)
    write_json(run_dir / 'run_config.json', {'mode': 'isolated_bm25' if args.offline_bm25 else 'isolated_hybrid',
        'embedding_model': settings.embedding_model, 'embedding_provider': settings.embedding_provider,
        'reranker': settings.reranker_provider, 'top_k':20, 'warmup':20, 'repeats':3, 'concurrency':1,
        'timeout_seconds':120, 'seed':20260916, 'snapshot_sha256':digest(ROOT/'snapshot/corpus.sqlite3'),
        'corpus_sha256':digest(ROOT/'corpus.jsonl'), 'queries_sha256':digest(ROOT/'queries.jsonl'),
        'hardware':{'platform':platform.platform(),'cpu':platform.processor(),'logical_cpu_count':os.cpu_count()},
        'latency_boundary':'Pipeline.retrieve entry to full return, excluding process transport and metric calculation',
        'non_rerank_latency':'Same-call total minus measured reranker duration; estimate, not a separate no-reranker run.',
        'cache_policy':'20 warmup requests, no artificial cache', 'quality_repeat':1,
        'cold_start':'Worker initialization separate; not included in warm latency'})
    ctx = multiprocessing.get_context('spawn')
    worker = None
    def start():
        parent, child = ctx.Pipe()
        process = ctx.Process(target=isolated_worker, args=(child, settings), daemon=True)
        process.start()
        if not parent.poll(120) or not parent.recv().get('ready'):
            process.terminate()
            process.join()
            raise RuntimeError('Isolated worker initialization failed')
        return process, parent
    worker, pipe = start()
    queries = read_jsonl(ROOT/'queries.jsonl')
    schedule = [('warmup', i+1, queries[i % len(queries)]) for i in range(20)]
    rng = random.Random(20260916)
    for repeat in range(1,4):
        ordered = list(queries)
        rng.shuffle(ordered)
        schedule.extend(('formal', repeat, q) for q in ordered)
    print(str(run_dir), flush=True)
    try:
        for index,(phase,repeat,q) in enumerate(schedule):
            row = {'phase':phase,'repeat':repeat,'query_id':q['query_id'],'results':[],
                   'status':'ok','degraded':False}
            pipe.send(q['question'])
            if not pipe.poll(120):
                worker.terminate()
                worker.join()
                pipe.close()
                row.update(status='timeout',latency_ms=120000)
                append_json(run_dir/'runs.jsonl',row)
                # A restart changes cache state; end this run instead of pretending warm continuity.
                break
            message = pipe.recv()
            row['latency_ms'] = message['latency_ms']
            if 'error' in message:
                row.update(status='error',error_type=message['error'])
            else:
                response = message['response']
                row.update(request_id=response['request_id'],degraded=response['degraded'],
                           degradation_reason=response['degradation_reason'],reranker_ms=message['reranker_ms'],
                           non_rerank_estimate_ms=message['non_rerank_estimate_ms'])
                row['results'] = [{'rank':i,'doc_id':e['chunk_id'],'score':e['score']}
                                   for i,e in enumerate(response['results'],1)]
            append_json(run_dir/'runs.jsonl',row)
            if (index+1)%10 == 0:
                print(json.dumps({'completed':index+1,'of':200,'last_latency_ms':round(row['latency_ms'])}),flush=True)
    finally:
        if worker.is_alive():
            pipe.send(None)
            worker.join(10)
            if worker.is_alive():
                worker.terminate()
                worker.join()
    print(json.dumps({'summary':str(run_dir/'summary.json'),'formal_requests':summarize(run_dir)['formal_requests']}))


def main():
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest='command', required=True)
    run = subs.add_parser('http')
    run.add_argument('--base-url', default='http://127.0.0.1:8000')
    run.add_argument('--timeout', type=int, default=120)
    isolated = subs.add_parser('isolated')
    isolated.add_argument('--offline-bm25', action='store_true')
    score = subs.add_parser('score')
    score.add_argument('run_dir', type=Path)
    score.add_argument('--provisional', action='store_true')
    score.add_argument('--qrels', type=Path, default=None)
    args = parser.parse_args()
    if args.command == 'http':
        run_http(args)
    elif args.command == 'isolated':
        run_isolated(args)
    else:
        path = args.run_dir.resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError('Run directory must be inside benchmark')
        print(json.dumps(summarize(path, provisional=args.provisional, qrels_path=args.qrels), ensure_ascii=False))


if __name__ == '__main__':
    main()
