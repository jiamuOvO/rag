# -*- coding: utf-8 -*-
"""Derive gold chunk sets by literal co-occurrence *inside one window*, and emit the YAML.

A chunk counts as relevant only when every defining literal of the fact occurs within a single
`WINDOW`-character span of the chunk text.  Plain "contains all literals" is too loose: it also
matches appendix tables and reference lists that mention the numbers pages apart.

Read-only.  No model calls.  Re-run after any re-ingestion to refresh the label set.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from rag.security import ANONYMOUS          # noqa: E402
from rag.store import Store                 # noqa: E402

WINDOW = 400

CASES = [
    dict(
        id="exact_temperature_yield",
        question="Which xylose experiment reported 71.1% furfural yield at 130 C?",
        literals=["71.1", "130"],
        note="The sentence pairing 71.1% with 130 C in the SnCl4 paper.",
    ),
    dict(
        id="des_high_yield",
        question="Which experiment reported 89.5% furfural yield at 157.3 C in 1.74 min?",
        literals=["89.5", "157.3", "1.74"],
        note="Deep-eutectic-solvent paper, abstract and conclusions.",
    ),
    dict(
        id="cmf_temperature_peak",
        question="At which temperatures did the furfural molar yield peak at 88.8 and 96.8%?",
        literals=["88.8", "96.8"],
        note="Chloromethylfurfural paper, Figure 13 discussion.",
    ),
    dict(
        id="three_value_comparison",
        question="Compare the furfural yields obtained at 120, 130 and 140 C.",
        literals=["66.5", "71.1", "70.2"],
        note="Single sentence listing all three yields with their residence times.",
    ),
    dict(
        id="xylan_residence_time",
        question="What furfural yield did xylan give after 30 min and at its maximum?",
        literals=["41.7", "50.1"],
        note="Xylan conversion experiment in the EMIMBr/SnCl4 system at 130 C.",
    ),
]


def windowed(text: str, literals: list[str], window: int = WINDOW) -> bool:
    """True when some span of `window` chars contains every literal."""
    starts = []
    for literal in literals:
        positions = []
        at = text.find(literal)
        while at != -1:
            positions.append(at)
            at = text.find(literal, at + 1)
        if not positions:
            return False
        starts.append(positions)
    for anchor in starts[0]:
        low, high = anchor - window, anchor + window
        if all(any(low <= p <= high for p in positions) for positions in starts[1:]):
            return True
    return False


parser = argparse.ArgumentParser(description="Re-derive tests/retrieval_gold.yaml")
parser.add_argument("--db", type=Path, default=Path("var/rag.sqlite3"),
                    help="database to scan (default: the live one; opened read-only)")
parser.add_argument("--out", type=Path, default=Path("tests/retrieval_gold.yaml"))
args = parser.parse_args()

if not args.db.exists():
    sys.exit(f"! database not found: {args.db}\n"
             f"  (sqlite would happily create an empty one, which silently wipes the gold set)")

chunks = Store(args.db).scoped_chunks(
    ANONYMOUS.tenant_id, ANONYMOUS.subject, collection_ids=[], include_official=True)
print(f"corpus: {len(chunks)} chunks from {args.db}\n")
if not chunks:
    sys.exit("! the scan returned no chunks - refusing to overwrite the gold set with nothing")

lines = [
    "# Gold labels for retrieval quality metrics (Recall@K / Precision@K / HitRate@K / MRR / nDCG@K).",
    "#",
    "# Derived by scanning the production scope for chunks where every defining literal of the fact",
    f"# occurs inside a single {WINDOW}-character window, then hand-checked against the printed snippet.",
    "# A chunk whose numbers appear only in a far-away table or reference entry is NOT labelled",
    "# relevant.  Re-derive with tests/derive_gold.py after any re-ingestion.",
    "#",
    "# caveat: the questions name exact literals, so a perfect score is achievable by literal",
    "# matching alone.  These metrics measure whether the retriever surfaces the page that states",
    "# the fact - not whether it 'understands' the question.",
    "version: 1",
    "window: %d" % WINDOW,
    "cases:",
]

labelled = []
for case in CASES:
    hits = sorted(c["chunk_id"] for c in chunks if windowed(c["text"], case["literals"]))
    labelled.append({"id": case["id"], "hits": hits})
    print(f"# {case['id']:<26} {len(hits)} chunks")
    for c in chunks:
        if c["chunk_id"] in hits:
            print(f"    {c['chunk_id']}  p{c['page_start']}-{c['page_end']}  {c['paper_name'][:46]}")
    lines.append(f"  - id: {case['id']}")
    lines.append(f"    question: {case['question']}")
    lines.append(f"    literals: {json.dumps(case['literals'], ensure_ascii=False)}")
    lines.append(f"    note: {case['note']}")
    lines.append("    relevant_chunk_ids:")
    for cid in hits:
        lines.append(f"      - {cid}")

total = sum(len(c["hits"]) for c in labelled)
if total == 0:
    sys.exit("! every case came back empty - refusing to overwrite the gold set with nothing")

Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\nwrote {args.out} ({total} relevant chunks)")
