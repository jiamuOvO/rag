"""Opt-in loopback-only bridge: IDs from the frozen benchmark, never arbitrary prompts or keys."""
from __future__ import annotations

import hmac
import os
import sys
import threading
from pathlib import Path
from typing import List, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from evaluate import read_jsonl
from freeze import digest
from annotate import request_batch


class BatchRequest(BaseModel):
    doc_ids: List[str] = Field(min_length=1, max_length=32)
    query_ids: List[str] = Field(min_length=1, max_length=60)
    corpus_sha256: str
    queries_sha256: str
    phase: Literal['first_pass','review','adjudication']='first_pass'

    model_config = {'extra': 'forbid'}


def install(app, pipeline):
    token = os.environ.get('RAG_BENCHMARK_TOKEN', '')
    if len(token) < 32:
        raise ValueError('Benchmark bridge requires a fresh high-entropy local token')
    docs = {d['doc_id']: d for d in read_jsonl(ROOT/'corpus.jsonl')}
    queries = {q['query_id']: q for q in read_jsonl(ROOT/'queries.jsonl')}
    hashes = (digest(ROOT/'corpus.jsonl'), digest(ROOT/'queries.jsonl'))
    slot = threading.BoundedSemaphore(min(4,max(1,pipeline.settings.chat_max_concurrency)))

    def authorize(request):
        if request.client is None or request.client.host not in {'127.0.0.1','::1','localhost','testclient'}:
            raise HTTPException(403, 'Loopback only')
        if not hmac.compare_digest(request.headers.get('x-benchmark-token',''),token):
            raise HTTPException(401, 'Benchmark authorization required')

    @app.get('/_benchmark/status', include_in_schema=False)
    def status(request: Request):
        authorize(request)
        return {'ready':bool(pipeline.settings.chat_api_key), 'model':pipeline.settings.chat_model,
                'corpus_sha256':hashes[0], 'queries_sha256':hashes[1],
                'documents':len(docs),'queries':len(queries),'protocol':'compact-v2',
                'max_concurrency':min(4,max(1,pipeline.settings.chat_max_concurrency)),
                'dynamic_model_options':True}

    @app.post('/_benchmark/annotate', include_in_schema=False)
    def annotate_batch(body: BatchRequest, request: Request):
        authorize(request)
        if (body.corpus_sha256,body.queries_sha256) != hashes:
            raise HTTPException(409,'Frozen benchmark hash mismatch')
        if len(set(body.doc_ids))!=len(body.doc_ids) or len(set(body.query_ids))!=len(body.query_ids):
            raise HTTPException(422,'Duplicate IDs')
        if any(d not in docs for d in body.doc_ids) or any(q not in queries for q in body.query_ids):
            raise HTTPException(422,'Unknown benchmark IDs')
        if sum(len(docs[d]['text']) for d in body.doc_ids)>48000:
            raise HTTPException(422,'Batch text budget exceeded')
        if not slot.acquire(blocking=False):
            raise HTTPException(429,'Annotation already in progress')
        try:
            rows,usage=request_batch(pipeline.settings,[queries[q] for q in body.query_ids],[docs[d] for d in body.doc_ids],phase=body.phase)
            return {'judgments':rows,'usage':usage,'model':pipeline.settings.chat_model}
        except Exception as exc:
            # The response never contains remote error bodies, request payloads, URLs, or credentials.
            detail={'code':'BENCHMARK_UPSTREAM_ERROR',
                    'message':type(exc).__name__+' upstream_status='+str(getattr(exc,'code',None))}
            if isinstance(exc,(ValueError,KeyError)):
                detail['message']+=' '+str(exc)
            raise HTTPException(502,detail) from None
        finally:
            slot.release()
