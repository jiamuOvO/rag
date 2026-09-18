"""Resolve the pending-review list one record at a time, and hand the undecidable ones over.

Three outcomes per (question, block), never more:

  original_correct   what is on disk stands. The reason text was loose (e.g. it cites an L1 rule
                     while the grade is 2), or the located risk does not apply to this record.
  already_corrected  a definite correction had already been applied and is recorded in the ledger.
  needs_user         unresolvable from the artefacts alone -> handed to the user, with what was
                     verified and what was not.

Discipline: a fact whose frozen quote does NOT appear in the block is a LOCATOR, never grounds to
downgrade -- the same fact can be stated in different words. Nothing here rewrites a grade.

Usage: python -B tests/biomass_furan/resolve_pending_review.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "semantic_pending_resolution.json"
sys.path.insert(0, str(ROOT))
from verify_judgments import match_level  # noqa: E402


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
    pending = json.loads((ROOT / "semantic_pending_review.json").read_text(encoding="utf-8"))
    ledger = {(c["query_id"], c["doc_id"]): c
              for c in json.loads((ROOT / "semantic_corrections.json").read_text(encoding="utf-8"))}

    # de-duplicate: one row per (question, block), keeping every reason it was listed under
    grouped = defaultdict(set)
    for p in pending:
        if p.get("doc_id"):
            grouped[(p["query_id"], p["doc_id"])].add(p.get("kind", "unknown"))

    resolved = {"original_correct": [], "already_corrected": [], "needs_user": []}
    for (qid, doc_id), kinds in sorted(grouped.items()):
        row = labels.get((qid, doc_id))
        doc = corpus.get(doc_id)
        q = queries.get(qid)
        if not row or not doc or not q:
            resolved["needs_user"].append({"query_id": qid, "doc_id": doc_id,
                                           "kinds": sorted(kinds), "why": "记录或语料缺失"})
            continue
        if (qid, doc_id) in ledger:
            resolved["already_corrected"].append({
                "query_id": qid, "doc_id": doc_id, "kinds": sorted(kinds),
                "grade_now": row.get("relevance"),
                "correction": ledger[(qid, doc_id)]["kind"],
                "rule": ledger[(qid, doc_id)].get("rule", "")[:160]})
            continue
        facts = q.get("fact_evidence") or []
        text = doc.get("text", "")
        supported = sum(1 for f in facts if match_level(f.get("quote", ""), text)[0] <= 2)
        record = {"query_id": qid, "doc_id": doc_id, "kinds": sorted(kinds),
                  "grade": row.get("relevance"),
                  "facts_total": len(facts), "facts_quote_hits": supported,
                  "named_source": q.get("source_group") or None,
                  "block_source": doc.get("source_id"),
                  "reason": str(row.get("reason"))[:200]}
        if "section8_needs_adjudication" in kinds:
            record["why"] = ("题面确已点名来源，但「该块是否引述/归因于该文献」未能验证——"
                             "需要读原文裁决，不能按 source_group 差异降级")
            resolved["needs_user"].append(record)
        elif "P4_grade3_partial_fact_quote_hits" in kinds:
            record["why"] = (f"三级但必需事实原文命中 {supported}/{len(facts)}。"
                             f"命中不足只是定位信号（事实可换措辞），需读原块判断，不据此降级")
            resolved["needs_user"].append(record)
        elif "P1_reason_cites_L1_but_grade_high" in kinds:
            record["why"] = ("理由引用 L1 规则但等级为 2/3。同论文/同体系的「数据基础」本身可以是 2 级"
                             "直接证据，理由措辞松不等于等级错——无其他证据时维持原判")
            resolved["original_correct"].append(record)
        else:
            resolved["needs_user"].append({**record, "why": "未归类，交用户"})

    OUT.write_text(json.dumps({
        "note": "去重后逐条处置。needs_user 是交给用户的清单，不是自动改级结果。",
        "pending_raw": len(pending), "unique_pairs": len(grouped),
        "counts": {k: len(v) for k, v in resolved.items()},
        "detail": resolved,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"待定原始条目 {len(pending)}｜按（题目,块）去重后 {len(grouped)} 条")
    for k, v in resolved.items():
        print(f"  {k}: {len(v)}")
    print()
    for k in ("already_corrected", "original_correct"):
        if resolved[k]:
            print(f"--- {k} 前 5 条 ---")
            for r in resolved[k][:5]:
                print(f"  {r['query_id']} {r['doc_id'][:18]} 等级={r.get('grade')} kinds={r['kinds']}")
    need = resolved["needs_user"]
    if need:
        print(f"\n--- 交用户裁决 {len(need)} 条，按原因 ---")
        for k, n in Counter(r["why"][:26] for r in need).most_common():
            print(f"  {n:4d}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
