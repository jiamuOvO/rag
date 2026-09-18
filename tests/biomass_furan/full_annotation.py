"""Bounded concurrent exhaustive annotation; adaptive splits preserve every pending pair."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import math
import sys
import time
import urllib.error

sys.dont_write_bytecode=True
from annotate import connect, export, bridge_request, PROMPT_VERSION
from evaluate import read_jsonl
from freeze import ROOT, write_json


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--bridge',default='http://127.0.0.1:8000')
    p.add_argument('--workers',type=int,choices=[1,2,3,4],default=4)
    p.add_argument('--doc-batch-size',type=int,choices=[4,8,16,32],default=32)
    p.add_argument('--max-batches',type=int)
    p.add_argument('--review',action='store_true',help='Independent second pass after complete first-pass labels')
    args=p.parse_args()
    status=bridge_request(args.bridge)
    if status.get('protocol')!='compact-v2':
        raise SystemExit('Restart the service with -Benchmark to load compact-v2.')
    conn=connect()
    docs=read_jsonl(ROOT/'corpus.jsonl')
    queries=read_jsonl(ROOT/'queries.jsonl')
    labels={(q,d):g for q,d,g in conn.execute('SELECT query_id,doc_id,relevance FROM judgments')}
    targets=None
    if args.review:
        if len(labels)!=len(docs)*len(queries):
            raise SystemExit('Complete all first-pass judgments before review.')
        targets={pair for pair,g in labels.items() if g>0}
        source={d['doc_id']:d['source_id'] for d in docs}
        strata=defaultdict(list)
        for pair,g in labels.items():
            if g==0:
                strata[(pair[0],source[pair[1]])].append(pair)
        for pairs in strata.values():
            # Shared random document order allows rectangular batches while retaining >=10%
            # zero sampling inside EVERY question/source stratum.
            pairs.sort(key=lambda pair:hashlib.sha256((pair[1]+'20260916').encode()).hexdigest())
            targets.update(pairs[:math.ceil(len(pairs)*0.1)])
        candidates=defaultdict(list)
        for path in (ROOT/'results').glob('*/runs.jsonl'):
            for r in read_jsonl(path):
                if r['phase']=='formal' and r['repeat']==1:
                    targets.update((r['query_id'],d['doc_id']) for d in r['results'][:5])
                    candidates[r['query_id']].extend(d['doc_id'] for d in r['results'])
        for path in (ROOT/'results').glob('*/stages.jsonl'):
            for r in read_jsonl(path):
                if r['repeat']==1:
                    candidates[r['query_id']].extend(d['doc_id'] for d in r['candidates'] if d['score']>0)
        hard_negatives={}
        for q in queries:
            qid=q['query_id']
            selected=[did for did in dict.fromkeys(candidates[qid]) if labels[(qid,did)]<=1][:5]
            hard_negatives[qid]=[{'doc_id':did,'relevance':labels[(qid,did)]} for did in selected]
            targets.update((qid,did) for did in selected)
        write_json(ROOT/'hard_negatives.json',{'selection':'First five judged binary-nonrelevant blocks from actual ranked candidates.',
                                              'queries':hard_negatives})
        known={(q,d) for q,d in conn.execute('SELECT query_id,doc_id FROM reviews')} & targets
        write_json(ROOT/'review_sampling.json',{'expected_pairs':len(targets),
            'rule':'All nonzero pairs; >=10% zeros per query/source using shared random document ordering; actual Top-5.',
            'seed':20260916,'human_review':False})
    else:
        known=set(labels)
    grouped=defaultdict(list)
    for d in docs:
        missing=tuple(i for i,q in enumerate(queries) if (q['query_id'],d['doc_id']) not in known
                      and (targets is None or (q['query_id'],d['doc_id']) in targets))
        if missing:
            grouped[missing].append(d)
    pending=deque()
    for indices,values in grouped.items():
        qs=[queries[i] for i in indices]
        batch,size=[],0
        for d in values:
            if batch and (len(batch)>=args.doc_batch_size or size+len(d['text'])>40000):
                pending.append((qs,batch,0))
                batch,size=[],0
            batch.append(d)
            size+=len(d['text'])
        if batch:
            pending.append((qs,batch,0))
    total=len(docs)*len(queries) if targets is None else len(targets)
    workers=min(args.workers,status['max_concurrency'])
    began=time.perf_counter()
    done=0
    failed=[]
    inflight={}
    print(json.dumps({'known_pairs':len(known),'expected_pairs':total,'initial_batches':len(pending),
                      'workers':workers,'protocol':PROMPT_VERSION}),flush=True)

    def checkpoint(state):
        write_json(ROOT/('review_status.json' if args.review else 'annotation_status.json'),{'status':state,'protocol':PROMPT_VERSION,
            'judged_pairs':len(known),'expected_pairs':total,'completed_batches_this_run':done,
            'queued_batches':len(pending),'inflight_batches':len(inflight),
            'failed_leaf_batches':len(failed),'elapsed_seconds':time.perf_counter()-began,
            'process_id':__import__('os').getpid()})

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while pending or inflight:
                if (ROOT/'runtime/pause_annotation').exists():
                    pending.clear()  # Finish in-flight batches; next launch rebuilds missing pairs from SQLite.
                while pending and len(inflight)<workers and (args.max_batches is None or done+len(inflight)<args.max_batches):
                    qs,batch,attempt=pending.popleft()
                    future=pool.submit(bridge_request,args.bridge,qs,batch,'review' if args.review else 'first_pass')
                    inflight[future]=(qs,batch,attempt)
                if not inflight:
                    break
                checkpoint('running')
                ready,_=wait(inflight,timeout=30,return_when=FIRST_COMPLETED)
                if not ready:
                    print(json.dumps({'judged_pairs':len(known),'inflight':len(inflight),'waiting_for_model':True}),flush=True)
                    continue
                for future in ready:
                    qs,batch,attempt=inflight.pop(future)
                    batch_id=hashlib.sha256((PROMPT_VERSION+'|'.join(q['query_id'] for q in qs)+'|'+
                                             '|'.join(d['doc_id'] for d in batch)).encode()).hexdigest()
                    try:
                        rows,usage=future.result()
                    except (ValueError,KeyError,TimeoutError,urllib.error.URLError) as exc:
                        detail=str(exc) if isinstance(exc,(ValueError,KeyError)) else type(exc).__name__
                        print(json.dumps({'batch_failed':batch_id[:12],'error':detail,'documents':len(batch),'queries':len(qs)}),flush=True)
                        if isinstance(exc,urllib.error.HTTPError) and exc.code in (401,403,404):
                            failed.append({'batch_id':batch_id,'error':type(exc).__name__,'http_status':exc.code})
                            pending.clear()
                        elif len(batch)>1:
                            middle=len(batch)//2
                            pending.appendleft((qs,batch[middle:],0))
                            pending.appendleft((qs,batch[:middle],0))
                        elif len(qs)>6:
                            middle=len(qs)//2
                            pending.appendleft((qs[middle:],batch,0))
                            pending.appendleft((qs[:middle],batch,0))
                        elif attempt<2:
                            pending.append((qs,batch,attempt+1))
                        else:
                            failed.append({'batch_id':batch_id,'error':detail,
                                'query_ids':[q['query_id'] for q in qs],'doc_ids':[d['doc_id'] for d in batch]})
                        continue
                    with conn:
                        for row in rows:
                            row.update(model=status['model'],batch_id=batch_id)
                            if args.review:
                                row.update(first_pass_grade=labels[(row['query_id'],row['doc_id'])],
                                    disagreement=labels[(row['query_id'],row['doc_id'])]!=row['relevance'],
                                    review_method='blind_same_model_second_pass',review_status='AI_second_pass')
                                conn.execute('INSERT OR REPLACE INTO reviews VALUES (?,?,?)',
                                    (row['query_id'],row['doc_id'],json.dumps(row,ensure_ascii=False)))
                            else:
                                conn.execute('INSERT OR IGNORE INTO judgments VALUES (?,?,?,?)',
                                   (row['query_id'],row['doc_id'],row['relevance'],json.dumps(row,ensure_ascii=False)))
                            known.add((row['query_id'],row['doc_id']))
                        conn.execute('INSERT OR REPLACE INTO batches VALUES (?,?)',(('review-' if args.review else '')+batch_id,json.dumps({
                            'model':status['model'],'protocol':PROMPT_VERSION,'usage':usage,'pairs':len(rows),'conflicts':[]})))
                    done+=1
                    checkpoint('running')
                    print(json.dumps({'completed_batches':done,'judged_pairs':len(known),'expected_pairs':total,
                                      'coverage':round(len(known)/total,5),'completion_tokens':usage.get('completion_tokens')}),flush=True)
                    if done%10==0 and not args.review:
                        export()
    finally:
        if args.review:
            disagreements=[json.loads(r[0]) for r in conn.execute('SELECT payload FROM reviews') if json.loads(r[0])['disagreement']]
            write_json(ROOT/'review_disagreements.json',disagreements)
        conn.close()
        export()
        write_json(ROOT/('review_failed_batches.json' if args.review else 'annotation_failed_batches.json'),failed)
        if args.review:
            checkpoint(('complete_no_disagreements' if not disagreements else 'adjudication_required') if len(known)==total else 'incomplete_resumable')
        else:
            checkpoint('first_pass_complete' if len(known)==total else 'incomplete_resumable')


if __name__=='__main__':
    main()
