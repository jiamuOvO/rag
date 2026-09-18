"""Build the reading material for the 100-combination semantic review. No rule-based judgement.

For each combination this emits exactly what a genuine review needs, and nothing that could stand
in for reading it: the question, the question's hard source constraint, every required fact with
its own evidence quote, and the COMPLETE block text. No title matching, no numeric anchors, no
regex verdicts -- those locator scripts are done and are not used here.

Output: semantic_review_input.txt (read in chunks), semantic_review_index.json (the order and the
identity of each item, so verdicts can be written back by (query_id, doc_id)).

Usage: python -B tests/biomass_furan/make_review_input.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_TXT = ROOT / "semantic_review_input.txt"
OUT_IDX = ROOT / "semantic_review_index.json"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    labels = {}
    for path in sorted((ROOT / "judgments").glob("q_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[(path.stem, row["doc_id"])] = row
    ledger = {(c["query_id"], c["doc_id"]): c for c in
              json.loads((ROOT / "semantic_corrections.json").read_text(encoding="utf-8"))}

    nj = [(x["query_id"], x["doc_id"]) for x in
          json.loads((ROOT / "needs_rejudge_pairs.json").read_text(encoding="utf-8"))]
    sp = [(x["query_id"], x.get("doc_id")) for x in
          json.loads((ROOT / "semantic_pending_review.json").read_text(encoding="utf-8"))
          if x.get("doc_id")]
    order, seen = [], set()
    for group, pairs in (("needs_rejudge", nj), ("semantic_pending", sp)):
        for qid, doc_id in pairs:
            if (qid, doc_id) in seen:
                continue
            seen.add((qid, doc_id))
            order.append({"query_id": qid, "doc_id": doc_id, "group": group,
                          "old_grade": labels.get((qid, doc_id), {}).get("relevance"),
                          "protected": (qid, doc_id) in ledger})

    lines, index = [], []
    for n, item in enumerate(order, 1):
        qid, doc_id = item["query_id"], item["doc_id"]
        q = queries.get(qid, {})
        doc = corpus.get(doc_id, {})
        row = labels.get((qid, doc_id), {})
        named = q.get("source_group") or []
        items = []
        for k, fact in enumerate(q.get("fact_evidence") or [], 1):
            items.append({"n": k, "fact": fact.get("fact"), "quote": fact.get("quote")})
        lines += [
            "=" * 100,
            f"[{n}/{len(order)}] {qid} | {doc_id} | group={item['group']} | "
            f"old_grade={item['old_grade']} | protected={item['protected']}",
            f"题面: {q.get('question')}",
            f"来源限定: 指定来源={named or '无'}｜本题块来源={doc.get('source_id')}｜"
            f"是否同一来源={'是' if doc.get('source_id') in named else ('否' if named else '本题不限定来源')}",
            f"难度/分区: {q.get('difficulty')}/{q.get('split')} | 类型 {q.get('question_type')} | answerable={q.get('answerable')}",
            f"旧理由: {str(row.get('reason'))[:200]}",
        ]
        for it in items:
            lines.append(f"必需事实{it['n']}: {it['fact']}")
            lines.append(f"   判据原文: {it['quote']}")
        lines += ["--- 完整原块 ---", " ".join(str(doc.get("text", "")).split()), ""]
        index.append({**item, "n": n, "facts": len(items)})

    OUT_TXT.write_text("\n".join(lines), encoding="utf-8")
    OUT_IDX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"阅读材料 {len(order)} 条 -> {OUT_TXT.name}"
          f"（{len(lines)} 行，{OUT_TXT.stat().st_size//1024} KB）")
    print(f"其中 needs_rejudge {sum(1 for x in order if x['group']=='needs_rejudge')}"
          f"｜semantic_pending {sum(1 for x in order if x['group']=='semantic_pending')}"
          f"｜含已保护修正 {sum(1 for x in order if x['protected'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
