"""Split one question's pool into fixed-size batches for a throughput trial.

Design constraints from the audit:
  * each batch carries the FULL question, required facts, and source metadata, so judging a
    batch never depends on which other candidates happened to land in it
  * batch files land in trial/<qid>/ and never touch the production judgments directory
  * identical rules version for every batch

Usage:
    python -B tests/biomass_furan/make_trial_batches.py q_0017 25
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINE_CAP = 1800


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def wrap(text: str, cap: int = LINE_CAP) -> str:
    flat = " ".join(text.split())
    return "\n".join(flat[i:i + cap] for i in range(0, len(flat), cap)) if flat else "(空块)"


def main() -> None:
    qid = sys.argv[1] if len(sys.argv) > 1 else "q_0017"
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 25

    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    pool = {p["query_id"]: p for p in read_jsonl(ROOT / "pool.jsonl")}
    q = queries[qid]
    entries = pool[qid]["pool"]

    out_dir = ROOT / "trial" / qid
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("batch_*.txt"):
        old.unlink()

    batches = [entries[i:i + size] for i in range(0, len(entries), size)]
    for n, batch in enumerate(batches, start=1):
        lines = [
            f"题目编号: {qid}",
            f"问题: {q['question']}",
            f"类型: {q['question_type']} | 难度: {q['difficulty']} | 语言: {q['language']} | 有答案: {q['answerable']}",
            "",
            "必须覆盖的事实:",
        ]
        for fact in q.get("required_facts") or []:
            lines.append(f"  - {fact}")
        lines += ["", f"参考答案: {q.get('reference_answer') or '(无)'}"]
        if q.get("source_group"):
            lines += ["", f"题目指定来源: {q['source_group']}  (详见作业规范对「指定来源」的处理规则)"]
        lines += [
            "",
            "=" * 64,
            f"本批共 {len(batch)} 个块（全题第 {n}/{len(batches)} 批）。逐个给出 0/1/2/3 等级。",
            "判断依据只能是本块正文与本批给出的题目要求，不得假设其他批次的候选情况。",
            "=" * 64,
            "",
        ]
        for j, entry in enumerate(batch, start=1):
            doc = corpus[entry["doc_id"]]
            lines.append(f"[{j}] doc_id={entry['doc_id']} | 来源={doc['source_id']} | 标题={doc['title']}")
            lines.append(wrap(doc["text"]))
            lines.append("")
        (out_dir / f"batch_{n:02d}.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"{qid}: 池 {len(entries)} 块 -> {len(batches)} 批 (每批 {size})")
    print(f"输出目录 {out_dir}")
    with (out_dir / "_batch_index.json").open("w", encoding="utf-8") as fh:
        json.dump({"query_id": qid, "pool_size": len(entries), "batch_size": size,
                   "batches": [{"n": i + 1, "docs": [e["doc_id"] for e in b]}
                               for i, b in enumerate(batches)]},
                  fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
