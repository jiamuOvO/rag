"""Read-only export of already recorded candidate rankings for benchmark request IDs."""
import argparse
import json
import sqlite3
from pathlib import Path
from freeze import ROOT, PROJECT, jsonl
from evaluate import read_jsonl


def main():
    p=argparse.ArgumentParser()
    p.add_argument('run_dir',type=Path)
    args=p.parse_args()
    run_dir=args.run_dir.resolve()
    if not run_dir.is_relative_to(ROOT):
        raise ValueError('Output must stay inside benchmark')
    config=json.loads((run_dir/'run_config.json').read_text(encoding='utf-8'))
    db=PROJECT/'var/rag.sqlite3' if config['mode']=='production_http' else ROOT/'runtime'/run_dir.name/'rag.sqlite3'
    rows=[]
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row
        for run in read_jsonl(run_dir/'runs.jsonl'):
            if run['phase']!='formal' or not run.get('request_id'):
                continue
            candidates=[dict(r) for r in c.execute('''SELECT retriever,rank,chunk_id AS doc_id,score,selected
                FROM query_candidates WHERE request_id=? ORDER BY retriever,rank''',(run['request_id'],))]
            rows.append({'query_id':run['query_id'],'repeat':run['repeat'],'request_id':run['request_id'],
                         'candidates':candidates,'note':'Logged candidates; final post-filter Top-20 is in runs.jsonl.'})
    jsonl(run_dir/'stages.jsonl',rows)
    print(json.dumps({'requests_with_stage_export':len(rows),'candidate_rows':sum(len(r['candidates']) for r in rows)}))


if __name__=='__main__':
    main()
