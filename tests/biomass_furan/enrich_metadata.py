"""One-time, recorded metadata correction before candidate release; text and IDs stay frozen."""
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from freeze import ROOT, digest, jsonl, write_json
from evaluate import read_jsonl

TITLES = {
 'paper_075d9e2d19ead8d2c43d1945': 'Supported gold- and silver-based catalysts for the selective aerobic oxidation of 5-(hydroxymethyl)-furfural to 2,5-furandicarboxylic acid and 5-hydroxymethyl-2-furancarboxylic acid',
 'paper_12e3bdcce4c31b870aa7923d': 'Catalytic Activity of Ti-based MXenes for the Hydrogenation of Furfural',
 'paper_14cccefc7706a12ec4946b02': 'Non-methane organic gas emissions from biomass burning: identification, quantification, and emission factors from PTR-ToF during the FIREX 2016 laboratory experiment',
 'paper_2017561758c5ec9b1eb23c75': 'Commercial Furfural: Its Properties and Uses',
 'paper_598a714a8ddb6a434683a20c': 'Emission of trace gases and aerosols from biomass burning – an updated assessment',
 'paper_690152f37c343d6e918bfcdc': 'Characterization and genomic analysis of kraft lignin biodegradation by the beta-proteobacterium Cupriavidus basilensis B-8',
 'paper_6929b5ed6d266478a534fad9': 'An overview of the applications of furfural and its derivatives',
 'paper_6ce2a9ed7e4402096086cbcc': 'Catalytic Conversion of Nonfood Woody Biomass Solids to Organic Liquids',
 'paper_70d3eea63983807a728fa4bf': 'Pyrolysis of Furan in a Microreactor',
 'paper_84cb4ba466e9ab9be859fee9': 'Biomass burning emissions and potential air quality impacts of volatile organic compounds and other trace gases from fuels common in the US',
 'paper_972ce08e4e1d4c13a274afd6': 'Sustainable production of furan-based oxygenated fuel additives from pentose-rich biomass residues',
 'paper_a39f1f9d78514a2f2d7f78be': 'Lignocellulosic Biomass: A Sustainable Platform for Production of Bio-Based Chemicals and Polymers',
 'paper_b387619177a2a9fd5098969e': 'Emission factors for open and domestic biomass burning for use in atmospheric models',
 'paper_d1a4e8bfc7c58b30dda482f2': 'Production and Downstream Integration of 5-(Chloromethyl)furfural from Lignocellulose',
 'paper_d5bfa1893984e7871d3fd568': 'The critical role of lignin in lignocellulosic biomass conversion and recent pretreatment strategies: A comprehensive review',
 'paper_d7141e85602f5e23b4cbaef5': 'A Ship-in-a-bottle Strategy to Synthesize Encapsulated Intermetallic Nanoparticles: Green Catalysts for Furfural Hydrogenation',
 'paper_dc7cbcb44f231c61245467df': 'Beyond 2,5-furandicarboxylic acid: status quo, environmental assessment, and blind spots of furanic monomers for bio-based polymers',
 'paper_f278385c64f44abe171772f3': 'A review on biomass: importance, chemistry, classification, and conversion',
 'paper_fc29dce759c42c9a91aa8868': 'The conversion of lignocellulosics to levulinic acid',
 'paper_107512d3ed43f43f9bd8f45e': 'Furfural and 5-Hydroxymethylfurfural Production from Sugar Mixture Using Deep Eutectic Solvent/MIBK System',
 'paper_fb5f556ebb605bbffb218189': 'Hydrolysis of Hemicellulose and Derivatives—A Review of Recent Advances in the Production of Furfural',
}


def compact(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def main():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('metadata_revision'):
        print('Metadata already finalized.')
        return
    sources = read_jsonl(ROOT / 'sources.jsonl')
    corpus = read_jsonl(ROOT / 'corpus.jsonl')
    old_hash = digest(ROOT / 'corpus.jsonl')
    with sqlite3.connect((ROOT / 'snapshot/corpus.sqlite3').as_uri() + '?mode=ro', uri=True) as c:
        for s in sources:
            pages = c.execute('SELECT page_number,text FROM pages WHERE paper_id=? ORDER BY page_number LIMIT 3', (s['source_id'],)).fetchall()
            title = TITLES.get(s['source_id'], s['title'])
            matching = next((page for page, text in pages if title and compact(title) in compact(text)), None)
            if not title:
                raise ValueError('Missing title: ' + s['source_id'])
            s['title'] = title
            s['title_origin'] = 'snapshot_page_title_checked' if matching else 'pdf_metadata_pending_review'
            s['title_evidence_page'] = matching
            s['source_type'] = 'journal_article'
            # A DOI is attributed only when present on the first page and unambiguous.
            dois = set(re.findall(r'10\.\d{4,9}/[A-Za-z0-9._;()/:-]+', pages[0][1] if pages else ''))
            s['doi'] = next(iter(dois)).rstrip('.,;)') if len(dois) == 1 else None
            s['metadata_review_status'] = 'title_checked_optional_fields_partial' if matching else 'pending'
    smap = {s['source_id']: s for s in sources}
    for d in corpus:
        s = smap[d['source_id']]
        d.update(title=s['title'], source_type=s['source_type'], doi=s['doi'])
        d['metadata'].update(title_origin=s['title_origin'], title_evidence_page=s['title_evidence_page'],
                             metadata_review_status=s['metadata_review_status'])
        assert hashlib.sha256(d['text'].encode()).hexdigest() == d['metadata']['text_sha256']
    jsonl(ROOT / 'sources.jsonl', sources)
    jsonl(ROOT / 'corpus.jsonl', corpus)
    manifest['metadata_revision'] = {'revision': 1, 'at': datetime.now(timezone.utc).isoformat(),
        'previous_corpus_sha256': old_hash, 'reason': 'Correct PDF title metadata against frozen page text before first release.',
        'ids_and_text_unchanged': True}
    manifest['corpus_sha256'] = digest(ROOT / 'corpus.jsonl')
    write_json(ROOT / 'manifest.json', manifest)
    path = ROOT / 'runtime/annotations.sqlite3'
    if path.exists():
        with sqlite3.connect(path) as c:
            if c.execute('SELECT count(*) FROM batches').fetchone()[0]:
                raise ValueError('Semantic labeling already started; metadata revision requires a new version')
            c.execute('UPDATE metadata SET value=? WHERE key=?', (manifest['corpus_sha256'], 'corpus_sha256'))
    print(json.dumps({'source_titles': len(sources), 'page_verified': sum(s['title_evidence_page'] is not None for s in sources),
                      'ids_and_text_unchanged': True}))


if __name__ == '__main__':
    main()
