"""Locate labels that carry the same semantic defects the spot-check already found.

This script only LOCATES. It never changes a grade from reason text -- a reason string is a
statement about a judgement, not evidence for a new one. Every hit must be re-read against the
question, the required facts, the original block and the source before it is corrected, and any
correction goes into semantic_corrections.json, separately logged and protected from re-merge.

Three patterns, each taken from a confirmed defect in the spot-check:

  P1  reason invokes an L1 rule but the grade is 2/3
      L1a/L1b/L1c/L1d are by definition the auxiliary levels, so a grade-2/3 judgement that
      justifies itself with one of them is internally inconsistent.
  P2  the question names a source and the block is not from it, yet the grade is 2/3
      ANNOTATION_INSTRUCTIONS section 8 caps another paper's independent statement of the same
      fact at grade 1 for a source-naming question.
  P3  reason claims complete fact coverage but the grade is 2
      Grade 3 is the level for a chunk that alone covers all required facts.
  P4  grade 3 while the block does not carry every required fact of the question
      Grade 3 is defined as covering ALL required facts plus the hard qualifiers, and a
      multi-document question can only have grade-2 evidence. Fact coverage is measured with the
      frozen verifier tiers on each fact's own evidence quote, so this is field-based, not
      reason-based.

Usage: python -B tests/biomass_furan/screen_semantic_risk.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "semantic_risk_screen.json"
sys.path.insert(0, str(ROOT))
from verify_judgments import match_level  # noqa: E402

L1_CITED = re.compile(r"\bL1[abcd]\b|L1 规则|same system, different condition|same paper, method"
                      r"|different paper, comparable run|mechanism/background", re.I)
FULL_COVER = re.compile(r"覆盖全部|全部必需事实|完整覆盖|无需结合他块|alone covers|all required facts"
                        r"|covers every required|fully covers|covers all required", re.I)


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    labels = {}
    for path in sorted((ROOT / "judgments").glob("q_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[(path.stem, row["doc_id"])] = row
    corrections = {}
    if (ROOT / "semantic_corrections.json").exists():
        for c in json.loads((ROOT / "semantic_corrections.json").read_text(encoding="utf-8")):
            corrections[(c["query_id"], c["doc_id"])] = c

    hits = {"P1_L1_rule_with_high_grade": [], "P2_named_source_other_paper": [],
            "P3_claims_full_coverage_at_2": [], "P4_grade3_without_full_fact_coverage": []}
    for (qid, doc_id), row in sorted(labels.items()):
        q = queries.get(qid)
        doc = corpus.get(doc_id)
        if not q or not doc:
            continue
        grade = row.get("relevance")
        reason = str(row.get("reason") or "")
        facts = q.get("fact_evidence") or []
        entry = {"query_id": qid, "doc_id": doc_id, "grade": grade,
                 "doc_source": doc.get("source_id"),
                 "named_source": q.get("source_group") or None,
                 "reason": reason[:220],
                 "already_corrected": (qid, doc_id) in corrections}
        if grade in (2, 3) and L1_CITED.search(reason):
            hits["P1_L1_rule_with_high_grade"].append(entry)
        named = q.get("source_group") or []
        if grade in (2, 3) and named and doc.get("source_id") not in named:
            hits["P2_named_source_other_paper"].append(entry)
        if grade == 2 and FULL_COVER.search(reason):
            hits["P3_claims_full_coverage_at_2"].append(entry)
        if grade == 3 and facts:
            text = doc.get("text", "")
            supported = sum(1 for f in facts if match_level(f.get("quote", ""), text)[0] <= 2)
            if supported < len(facts):
                hits["P4_grade3_without_full_fact_coverage"].append(
                    {**entry, "facts_total": len(facts), "facts_supported": supported})

    OUT.write_text(json.dumps({
        "note": "仅定位，不自动改等级。每条需回读原题、必需事实、原块与来源后再决定。",
        "records_scanned": len(labels),
        "counts": {k: len(v) for k, v in hits.items()},
        "hits": hits,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"扫描正式标签 {len(labels)} 条")
    for k, v in hits.items():
        print(f"  {k}: {len(v)} 条（已修正 {sum(1 for e in v if e['already_corrected'])} 条）")
    for k, v in hits.items():
        if not v:
            continue
        print(f"\n--- {k} 前 8 条 ---")
        for e in v[:8]:
            named = ",".join(e["named_source"] or [])[:40]
            print(f"  {e['query_id']} {e['doc_id'][:18]} 等级={e['grade']} "
                  f"块来源={str(e['doc_source'])[:34]} 指定来源={named}")
            print(f"      {e['reason'][:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
