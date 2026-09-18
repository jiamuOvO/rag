"""Build the semantic spot-check worksheet: original question + required facts + original block.

diag_parse_failures.py only replays the parser, so it can speak about FORMAT and evidence
matching. It cannot say whether a grade is semantically right. This worksheet puts the three
things a semantic check actually needs side by side:

    the question as asked, the required facts, the assigned grade + reason, and the block text

Sampling: stratified over the four revision buckets that carry the most risk if wrong, plus the
newly added positives. Deterministic (fixed stride), so a re-run reproduces the same sample.

Usage: python -B tests/biomass_furan/make_spotcheck.py [--per-bucket 4]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "semantic_spotcheck_worksheet.txt"
sys.path.insert(0, str(ROOT))
from verify_judgments import match_level  # noqa: E402


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    per_bucket = 4
    if "--per-bucket" in sys.argv:
        per_bucket = int(sys.argv[sys.argv.index("--per-bucket") + 1])

    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    labels = {}
    for path in sorted((ROOT / "judgments").glob("q_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[(path.stem, row["doc_id"])] = row
    revisions = json.loads((ROOT / "merge_revisions.json").read_text(encoding="utf-8"))

    buckets = {"复核 1->0": [], "复核 降出正例(2/3->0/1)": [], "复核 降级(3->2)": [],
               "复核 升入正例(1->2/3)": []}
    for r in revisions:
        if r.get("change") != "recheck_superseded":
            continue
        old, new = r["old"]["relevance"], r["new"]["relevance"]
        if (old, new) == (1, 0):
            buckets["复核 1->0"].append(r)
        elif old >= 2 and new < 2:
            buckets["复核 降出正例(2/3->0/1)"].append(r)
        elif (old, new) == (3, 2):
            buckets["复核 降级(3->2)"].append(r)
        elif old < 2 and new >= 2:
            buckets["复核 升入正例(1->2/3)"].append(r)

    # Newly added positive pairs that no earlier round had graded.
    previous = set()
    for backup in sorted(ROOT.glob("judgments_backup_*")):
        for path in sorted(backup.glob("q_*.json")):
            for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
                previous.add((path.stem, row["doc_id"]))
        break
    added_pos = [{"query_id": q, "doc_id": d, "old": {"relevance": None},
                  "new": {"relevance": labels[(q, d)]["relevance"],
                          "reason": str(labels[(q, d)].get("reason"))[:200]}}
                 for (q, d) in sorted(labels) if (q, d) not in previous
                 and labels[(q, d)].get("relevance", 0) >= 2]
    buckets["本轮新增正例"] = added_pos

    lines, selected = [], 0
    for name, items in buckets.items():
        if not items:
            continue
        stride = max(1, len(items) // per_bucket)
        for r in items[::stride][:per_bucket]:
            selected += 1
            qid, did = r["query_id"], r["doc_id"]
            q = queries.get(qid, {})
            doc = corpus.get(did, {})
            row = labels.get((qid, did), {})
            lines.append("=" * 100)
            lines.append(f"[{name}] {qid} | {str(doc.get('title'))[:72]}")
            lines.append(f"  池内总数 {len(items)}，本桶取样 {len(items[::stride][:per_bucket])} 条")
            lines.append(f"  题    : {str(q.get('question'))[:200]}")
            lines.append(f"  难度/分区: {q.get('difficulty')}/{q.get('split')} | "
                         f"指定来源: {q.get('source_group') or '无'} | 该块来源: {doc.get('source_id')}")
            for i, fact in enumerate(q.get("fact_evidence") or [], 1):
                hit = match_level(fact["quote"], doc.get("text", ""))[0] <= 2
                lines.append(f"  必需事实{i}: {str(fact['fact'])[:110]}")
                lines.append(f"     该块是否含此事实的原文: {'是' if hit else '否'}｜判据 {fact['quote'][:90]!r}")
            lines.append(f"  等级: {r['old']['relevance']} -> {row.get('relevance')}"
                         f" | 记录来源 {row.get('source') or row.get('method') or 'agent_era'}")
            lines.append(f"  新理由: {str(row.get('reason'))[:240]}")
            quotes = row.get("evidence_quotes") or []
            if quotes:
                lines.append(f"  新引文({len(quotes)}): {quotes[0][:200]!r}")
            text = " ".join(str(doc.get("text", "")).split())
            lines.append(f"  原块正文前 700 字: {text[:700]}")
            lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"样本 {selected} 条（{len([b for b in buckets.values() if b])} 个分层）-> {OUT.name}")
    for name, items in buckets.items():
        if items:
            print(f"  {name}: 池 {len(items)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
