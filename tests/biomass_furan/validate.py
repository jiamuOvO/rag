"""Validate artifacts without confusing structural completion with gold-label completion."""
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from freeze import ROOT, PROJECT, digest, write_json
from evaluate import read_jsonl, read_qrels
from build_dataset import norm


def main():
    corpus = read_jsonl(ROOT / 'corpus.jsonl')
    queries = read_jsonl(ROOT / 'queries.jsonl')
    labels = read_qrels(ROOT / 'qrels.tsv')
    seeds = read_jsonl(ROOT / 'seed_judgments.jsonl')
    sources = read_jsonl(ROOT / 'sources.jsonl')
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    checks, errors, warnings = {}, [], []

    def check(name, value):
        checks[name] = bool(value)
        if not value:
            errors.append(name)

    docs = {d['doc_id']: d for d in corpus}
    qmap = {q['query_id']: q for q in queries}
    check('unique_doc_ids', len(docs) == len(corpus))
    check('unique_query_ids', len(qmap) == len(queries))
    check('corpus_manifest_hash', digest(ROOT / 'corpus.jsonl') == manifest['corpus_sha256'])
    check('snapshot_hash', digest(ROOT / 'snapshot/corpus.sqlite3') == manifest['snapshot_sha256'])
    check('frozen_source_count', len({d['source_id'] for d in corpus}) == manifest['retrievable_sources'])
    check('text_hashes', all(hashlib.sha256(d['text'].encode()).hexdigest() == d['metadata']['text_sha256'] for d in corpus))
    check('source_title_evidence', all(s['title'] and s.get('title_evidence_page') for s in sources))
    check('qrels_foreign_keys', all(q in qmap and all(d in docs for d in rows) for q, rows in labels.items()))
    check('query_count', len(queries) == 60)
    distributions = {key: dict(Counter(q[key] for q in queries)) for key in ('question_type','difficulty','language','split')}
    check('languages', distributions['language'] == {'zh': 42, 'en': 18})
    check('difficulties', distributions['difficulty'] == {'easy': 18, 'medium': 30, 'hard': 12})
    check('splits', distributions['split'] == {'dev': 12, 'test': 36, 'challenge': 12})
    check('types', distributions['question_type'] == dict(fact=9,definition=6,condition=9,mechanism=9,
                     data=6,comparison=6,multi_document=6,constraint=3,paraphrase=3,no_answer=3))
    facts_ok = all(f['doc_id'] in docs and f['source_id'] == docs[f['doc_id']]['source_id']
                   and norm(f['quote']) in norm(docs[f['doc_id']]['text']) for q in queries for f in q['fact_evidence'])
    check('fact_quotes_verbatim', facts_ok)
    check('answerable_seed_evidence', all(not q['answerable'] or any(r >= 2 for r in labels.get(q['query_id'], {}).values()) for q in queries))
    check('seed_quotes_verbatim', all(all(norm(quote) in norm(docs[s['doc_id']]['text']) for quote in s['evidence_quotes']) for s in seeds))
    source_splits = defaultdict(set)
    for q in queries:
        for did, rel in labels.get(q['query_id'], {}).items():
            if rel >= 2:
                source_splits[docs[did]['source_id']].add(q['split'])
    leakage = {s: sorted(v) for s, v in source_splits.items() if len(v) > 1}
    check('known_positive_source_leakage', not leakage)
    # All original seed queries relied on these sources; they must remain dev only.
    legacy_sources = {'paper_db1a45d39737c05f08fff0fa','paper_0cc8554d80109d5ddfe91435','paper_d1a4e8bfc7c58b30dda482f2'}
    check('legacy_tuning_sources_dev_only', all(source_splits[s] <= {'dev'} for s in legacy_sources))
    no_answer_conflicts = [{'query_id': q['query_id'], 'doc_id': d, 'relevance': r}
                          for q in queries if not q['answerable'] for d, r in labels.get(q['query_id'], {}).items() if r >= 2]
    check('known_no_answer_conflicts', not no_answer_conflicts)
    count = sum(len(rows) for rows in labels.values())
    expected = len(corpus) * len(queries)
    full = count == expected and all(set(labels.get(q, {})) == set(docs) for q in qmap)
    ledger_count, ledger_judged, ledger_unjudged = 0, 0, 0
    with gzip.open(ROOT / 'annotation_records.jsonl.gz', 'rt', encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            qindex, dindex = divmod(ledger_count, len(corpus))
            if qindex >= len(queries) or row['query_id'] != queries[qindex]['query_id'] or row['doc_id'] != corpus[dindex]['doc_id']:
                raise ValueError('Ledger order/coverage/duplicate error')
            if row['relevance'] is None:
                ledger_unjudged += 1
                if row['doc_id'] in labels.get(row['query_id'], {}):
                    raise ValueError('Unjudged pair was exported as a qrel')
            else:
                ledger_judged += 1
                if labels[row['query_id']].get(row['doc_id']) != row['relevance']:
                    raise ValueError('Ledger/qrels mismatch')
            ledger_count += 1
    check('complete_pair_status_ledger', ledger_count == expected)
    check('judged_ledger_matches_qrels', ledger_judged == count)
    check('unjudged_not_zero', ledger_unjudged == expected - count)
    before = json.loads((ROOT / 'outside_hashes_before.json').read_text(encoding='utf-8'))
    changed = [name for name, sha in before.items() if not (PROJECT / name).exists() or digest(PROJECT / name) != sha]
    exceptions_path=ROOT/'authorized_scope_exceptions.json'
    exceptions=json.loads(exceptions_path.read_text(encoding='utf-8')) if exceptions_path.exists() else {}
    expected_changes=exceptions.get('files',{})
    unexpected=[name for name in changed if name not in expected_changes or not (PROJECT/name).exists()
                or digest(PROJECT/name)!=expected_changes[name]['after_sha256']]
    check('protected_files_unchanged_except_authorized_bridge', not unexpected)
    duplicate_text_groups = defaultdict(list)
    for d in corpus:
        duplicate_text_groups[hashlib.sha256(norm(d['text']).encode()).hexdigest()].append(d['doc_id'])
    duplicate_groups = [g for g in duplicate_text_groups.values() if len(g) > 1]
    write_json(ROOT / 'duplicate_groups.json', duplicate_groups)
    if not full:
        warnings.append('全量语义标注尚未完成；缺失qrels为UNJUDGED，不可视为0；正式质量指标不可发布。')
    warnings.extend(['尚未完成具备化学背景人员的人工复核。',
                     '同模型复核不等于双人独立标注；不报告人工Cohen kappa。',
                     '多数题目明确指定研究来源；不能据此代表开放领域自然问题的全部难度。',
                     '同义改写与来源分组已按种子证据处理，最终跨来源检查仍依赖全量标注。'])
    review_path = ROOT / 'review_status.json'
    review = json.loads(review_path.read_text(encoding='utf-8')) if review_path.exists() else {'status': 'not_started'}
    conflict_path = ROOT / 'annotation_conflicts.json'
    conflicts = json.loads(conflict_path.read_text(encoding='utf-8')) if conflict_path.exists() else []
    runs = []
    for path in sorted((ROOT / 'results').glob('*/summary.json')):
        s = json.loads(path.read_text(encoding='utf-8'))
        runs.append({'path': str(path.relative_to(ROOT)), 'run_complete': s['run_complete'],
                     'metric_status': s['metric_status'], 'formal_requests': s['formal_requests']})
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'dataset_version': manifest['dataset_version'],
       'structural_validation_passed': not errors, 'checks': checks, 'errors': errors, 'warnings': warnings,
       'counts': {'sources': len(sources), 'chunks': len(corpus), 'queries': len(queries),
                  'expected_pairs': expected, 'judged_pairs': count, 'unjudged_pairs': expected-count,
                  'normalized_duplicate_groups': len(duplicate_groups)}, 'distributions': distributions,
       'semantic_annotation_complete': full, 'AI_review': review, 'unresolved_seed_model_conflicts': len(conflicts),
       'human_review': {'status': 'not_completed', 'reviewer_count': 0, 'kappa': None},
       'release_ready': not errors and full and not conflicts and review.get('status') == 'complete_no_disagreements',
       'original_human_acceptance_standard_met': False, 'source_split_leakage': leakage,
       'no_answer_conflicts': no_answer_conflicts, 'outside_protected_files_changed': changed,
       'unexpected_protected_file_changes': unexpected, 'authorized_scope_exceptions':exceptions,
       'production_writes': 'Only normal retrieval request logs permitted by subsequent user authorization.',
       'retrieval_runs': runs}
    write_json(ROOT / 'validation_report.json', report)
    print(json.dumps({k: report[k] for k in ('structural_validation_passed','semantic_annotation_complete','release_ready','counts')}, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
