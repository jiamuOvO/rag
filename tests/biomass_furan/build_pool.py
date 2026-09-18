"""TREC-style pooling: union of candidates observed across all judged retrieval runs.

Follows the standard TREC procedure -- take the top-d of every run, merge, deduplicate.
Documents outside the pool stay UNJUDGED and must never be scored as irrelevant.

Usage:
    python -B tests/biomass_furan/build_pool.py
    python -B tests/biomass_furan/build_pool.py --self-check
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = [
    ("http", ROOT / "results/http-20260916T063408115501Z/stages.jsonl"),
    ("isolated", ROOT / "results/isolated-20260916T064442079079Z/stages.jsonl"),
]
OUT = ROOT / "pool.jsonl"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def build_pool() -> dict[str, dict[str, dict[str, int]]]:
    """query_id -> doc_id -> {system_label: best_rank}"""
    pool: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for run_label, path in RUNS:
        for row in read_jsonl(path):
            qid = row["query_id"]
            for cand in row.get("candidates", []):
                system = f"{run_label}/{cand['retriever']}"
                ranks = pool[qid].setdefault(cand["doc_id"], {})
                rank = int(cand["rank"])
                if rank < ranks.get(system, 1 << 30):
                    ranks[system] = rank
    return pool


def main() -> None:
    pool = build_pool()
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}

    rows = []
    for qid, docs in pool.items():
        q = queries[qid]
        entries = sorted(
            ({"doc_id": d, "best_rank": min(r.values()), "systems": r} for d, r in docs.items()),
            key=lambda e: e["best_rank"],
        )
        rows.append({
            "query_id": qid,
            "question": q["question"],
            "question_type": q["question_type"],
            "difficulty": q["difficulty"],
            "language": q["language"],
            "answerable": q["answerable"],
            "split": q["split"],
            "required_facts": q.get("required_facts", []),
            "reference_answer": q.get("reference_answer"),
            "pool_size": len(entries),
            "pool": entries,
        })
    rows.sort(key=lambda r: r["query_id"])

    with OUT.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    total = sum(r["pool_size"] for r in rows)
    sizes = [r["pool_size"] for r in rows]
    print(f"题目 {len(rows)} | 池内总对数 {total} | 平均 {total / len(rows):.1f}")
    print(f"池大小范围 {min(sizes)} ~ {max(sizes)}")
    print(f"写出 {OUT}")


def _self_check() -> None:
    """Fail loudly if the pool is empty, missing a question, or implausibly sized."""
    pool = build_pool()
    queries = {q["query_id"] for q in read_jsonl(ROOT / "queries.jsonl")}
    assert len(pool) == len(queries) == 60, f"expected 60 questions, got {len(pool)}"
    sizes = [len(d) for d in pool.values()]
    assert all(0 < s < 500 for s in sizes), f"implausible per-question size {min(sizes)}~{max(sizes)}"
    total = sum(sizes)
    assert 1000 < total < 20000, f"implausible pool total {total}"
    print(f"self-check ok: {len(pool)} questions, {total} pairs, sizes {min(sizes)}~{max(sizes)}")


if __name__ == "__main__":
    import sys

    if "--self-check" in sys.argv:
        _self_check()
    else:
        main()
