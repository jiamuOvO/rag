"""Read-only production export; every output is confined to this directory."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Output must stay under tests/biomass_furan')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def jsonl(path, rows):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Output outside benchmark')
    with path.open('w', encoding='utf-8', newline='\n') as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')


def frozen_rows(conn, at):
    return conn.execute('''SELECT DISTINCT c.* FROM collection_documents d
        JOIN chunks c ON c.paper_id=d.paper_id
        WHERE d.tenant_id='default' AND d.scope_type='official'
        AND d.status IN ('ready','partial_failed')
        AND (d.expires_at IS NULL OR d.expires_at>?)
        AND NOT EXISTS (SELECT 1 FROM pages p WHERE p.paper_id=c.paper_id
            AND p.page_number BETWEEN c.page_start AND c.page_end AND p.retrievable=0)
        AND (SELECT count(*) FROM pages p WHERE p.paper_id=c.paper_id
            AND p.page_number BETWEEN c.page_start AND c.page_end)=c.page_end-c.page_start+1
        ORDER BY c.paper_id,c.ordinal,c.chunk_id''', (at,)).fetchall()


def main():
    manifest_path = ROOT / 'manifest.json'
    if manifest_path.exists():
        raise SystemExit('Frozen version already exists; do not silently overwrite it.')
    snapshot = ROOT / 'snapshot' / 'corpus.sqlite3'
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.exists():
        raise SystemExit('Snapshot exists without manifest; inspect before retrying.')
    at = datetime.now(timezone.utc).isoformat()
    outside = {}
    for folder in ('src', 'docs', 'scripts', 'data'):
        for p in (PROJECT / folder).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:
                outside[str(p.relative_to(PROJECT))] = digest(p)
    for p in PROJECT.iterdir():
        if p.is_file():
            outside[p.name] = digest(p)
    write_json(ROOT / 'outside_hashes_before.json', outside)
    source = PROJECT / 'var' / 'rag.sqlite3'
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(snapshot) as dst:
            src.backup(dst)
    with sqlite3.connect(snapshot.as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        papers = {r['paper_id']: dict(r) for r in conn.execute('SELECT * FROM papers')}
        rows = frozen_rows(conn, at)
        sources = []
        for pid in sorted({r['paper_id'] for r in rows}):
            p = papers[pid]
            # PDF metadata is advisory only; original filename is provenance, never an ID/title.
            title = None
            title_origin = None
            try:
                import fitz
                with fitz.open(p['file_path']) as pdf:
                    raw = (pdf.metadata.get('title') or '').strip()
                    if raw and len(raw) > 10 and not raw.lower().endswith(('.doc', '.docx')):
                        title, title_origin = raw, 'pdf_metadata_unverified'
            except (OSError, RuntimeError):
                pass
            sources.append({'source_id': pid, 'source_hash': p['source_hash'],
                            'original_filename': p['file_name'], 'title': title,
                            'title_origin': title_origin, 'source_type': 'unknown',
                            'status': p['status'], 'page_count': p['page_count']})
        smap = {s['source_id']: s for s in sources}
        corpus = []
        for r in rows:
            s = smap[r['paper_id']]
            corpus.append({'doc_id': r['chunk_id'], 'source_id': r['paper_id'],
                'title': s['title'], 'text': r['text'], 'source_type': s['source_type'],
                'section': r['section_path'] or None, 'year': None, 'authors': [], 'doi': None,
                'language': 'en', 'metadata': {
                    'page_start': r['page_start'], 'page_end': r['page_end'],
                    'ordinal': r['ordinal'], 'source_hash': s['source_hash'],
                    'original_filename': s['original_filename'],
                    'title_origin': s['title_origin'],
                    'parser_version': r['parser_version'], 'chunker_version': r['chunker_version'],
                    'token_count': r['token_count'], 'overlap_tokens': r['overlap_tokens'],
                    'text_sha256': hashlib.sha256(r['text'].encode()).hexdigest(),
                    'metadata_review_status': 'pending'}})
        models = [dict(r) for r in conn.execute('''SELECT provider,model,dimensions,count(*) count
                     FROM embeddings GROUP BY provider,model,dimensions''')]
        totals = {t: conn.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                  for t in ('papers', 'pages', 'chunks', 'embeddings')}
    jsonl(ROOT / 'corpus.jsonl', corpus)
    jsonl(ROOT / 'sources.jsonl', sources)
    write_json(manifest_path, {'dataset_version': '0.1.0-ai-candidate', 'frozen_at': at,
        'scope': {'tenant_id': 'default', 'subject': 'anonymous', 'include_official': True},
        'physical_counts': totals, 'retrievable_sources': len(sources),
        'retrievable_chunks': len(corpus), 'snapshot_sha256': digest(snapshot),
        'corpus_sha256': digest(ROOT / 'corpus.jsonl'),
        'chunker_versions': dict(Counter(r['chunker_version'] for r in rows)),
        'embedding_indexes': models, 'human_review': 'not_completed',
        'source_code_sha256': {str(p.relative_to(PROJECT)): digest(p)
                               for p in sorted((PROJECT / 'src').rglob('*.py'))}})
    print(json.dumps({'sources': len(sources), 'chunks': len(corpus), 'physical': totals}))


if __name__ == '__main__':
    main()
