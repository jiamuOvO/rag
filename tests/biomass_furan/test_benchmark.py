"""Small hand-calculated checks for metrics and incomplete-label safety."""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate import score_query, percentile, latency_stats
from annotate import parse_response


def test_graded_metrics_and_short_lists():
    gold = {'a': 3, 'b': 2, 'c': 1, 'x': 0}
    r = score_query(['c','b','x'], gold)
    assert r['Recall@5'] == 0.5
    assert r['Precision@5'] == 0.2
    assert r['HitRate@1'] == 0
    assert r['MRR@10'] == 0.5
    expected = (1 + 3 / math.log2(3)) / (7 + 3 / math.log2(3) + 1 / math.log2(4))
    assert r['nDCG@5'] == pytest.approx(expected)
    assert score_query([], gold)['MRR@10'] == 0
    assert score_query(['x'] * 0 + ['a'], gold)['Precision@20'] == 1 / 20


def test_mrr_cutoff_unknown_and_duplicates():
    gold = {**{str(i): 0 for i in range(10)}, 'yes': 3}
    assert score_query(list(map(str, range(10))) + ['yes'], gold)['MRR@10'] == 0
    with pytest.raises(ValueError):
        score_query(['yes','yes'], gold)
    with pytest.raises(ValueError):
        score_query(['unknown'], gold)
    with pytest.raises(ValueError):
        score_query([], {'a': 1})


def test_no_answer_and_latency():
    assert score_query([], {}, answerable=False) == {'no_answer_nonempty': 0, 'returned_count': 0}
    assert score_query(['x'], {'x': 0}, answerable=False)['no_answer_nonempty'] == 1
    assert percentile([1,2,3,4], 50) == 2.5
    assert percentile([1,2,3,4], 95) == pytest.approx(3.85)
    assert latency_stats([100, 120000])['mean_ms'] == 60050
    assert percentile([], 99) is None


def test_semantic_batch_requires_all_pairs_and_real_quotes():
    q = [{'query_id': 'q', 'answerable': True, 'fact_evidence': [{'fact_id': 'f1'}]}]
    d = [{'doc_id': 'a', 'text': 'Yield was 60%.'}, {'doc_id': 'b', 'text': 'Unrelated polymer.'}]
    result = {'grades': {'q': [3, 0]}, 'zero_reasons': {'q': 'The other passage gives no yield data.'},
              'evidence': {'q': {'a': {'quote': 'Yield was 60%.', 'reason': 'Direct measurement.', 'fact_ids': ['f1']}}}}
    rows = parse_response(json.dumps(result), q, d)
    assert len(rows) == 2 and rows[1]['relevance'] == 0
    result['grades']['q'] = [3]
    with pytest.raises(ValueError):
        parse_response(json.dumps(result), q, d)
    result['grades']['q'] = [3, 0]
    result['evidence']['q']['a']['quote'] = 'Yield was 90%.'
    with pytest.raises(ValueError):
        parse_response(json.dumps(result), q, d)


def test_bridge_auth_frozen_ids_and_no_arbitrary_prompts(monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import bridge
    monkeypatch.setenv('RAG_BENCHMARK_TOKEN','a'*64)
    monkeypatch.setattr(bridge,'read_jsonl',lambda path:[{'doc_id':'d','text':'actual passage'}] if path.name=='corpus.jsonl' else [{'query_id':'q'}])
    monkeypatch.setattr(bridge,'digest',lambda path:'hash')
    monkeypatch.setattr(bridge,'request_batch',lambda settings,queries,docs,phase='first_pass':([{'checked':True}],{}))
    app=FastAPI()
    bridge.install(app,SimpleNamespace(settings=SimpleNamespace(chat_api_key='never-return-this',chat_model='test',chat_max_concurrency=4)))
    client=TestClient(app,client=('127.0.0.1',12345))
    assert client.get('/_benchmark/status').status_code==401
    headers={'x-benchmark-token':'a'*64}
    status=client.get('/_benchmark/status',headers=headers)
    assert status.status_code==200 and 'never-return-this' not in status.text
    payload={'doc_ids':['d'],'query_ids':['q'],'corpus_sha256':'hash','queries_sha256':'hash'}
    assert client.post('/_benchmark/annotate',headers=headers,json=payload).status_code==200
    assert client.post('/_benchmark/annotate',headers=headers,json={**payload,'prompt':'arbitrary'}).status_code==422
    assert client.post('/_benchmark/annotate',headers=headers,json={**payload,'doc_ids':['unknown']}).status_code==422
    assert client.post('/_benchmark/annotate',headers=headers,json={**payload,'corpus_sha256':'changed'}).status_code==409
    remote=TestClient(app,client=('203.0.113.1',12345))
    assert remote.get('/_benchmark/status',headers=headers).status_code==403


def test_compact_explicit_broadcast_and_shared_proof():
    from annotate import parse_compact
    qs=[{'query_id':'q0','answerable':True,'fact_evidence':[]},
        {'query_id':'q1','answerable':False,'fact_evidence':[]}]
    ds=[{'doc_id':'a','text':'Yield was 60%.'},{'doc_id':'b','text':'Other chemistry.'}]
    payload={'grades':['30',0],'zero_reasons':['Other text lacks yield.','No target evidence.'],
             'proofs':[{'document':0,'queries':[0],'quote':'Yield was 60%.','reason':'Direct yield.'}]}
    result=parse_compact(json.dumps(payload),qs,ds)
    assert len(result)==4 and [r['relevance'] for r in result]==[3,0,0,0]
    payload['grades'][0]='3'
    with pytest.raises(ValueError):
        parse_compact(json.dumps(payload),qs,ds)
    payload['grades'][0]='30'
    payload['proofs'][0]['quote']='Yield was 90%.'
    with pytest.raises(ValueError):
        parse_compact(json.dumps(payload),qs,ds)
