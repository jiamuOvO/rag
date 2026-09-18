"""Reconcile the known-positive sets and the run accounting before the release gap fill starts.

Answers three audit questions, all locally:

  1. seed positives vs existing positives vs their UNION, and how many fall OUTSIDE the scored
     Top-K. Positives outside the Top-K must stay pending -- they are not dropped for being
     un-retrieved.
  2. the full planned run list with per-run success / failure counts, so a failing run cannot
     silently disappear from the denominator.
  3. whether any scored run returned fewer than TOP_K items -- recorded as-is, never padded.

Usage: python -B tests/biomass_furan/reconcile.py
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOP_K = 20
RUNS = [("http", "http-20260916T063408115501Z"), ("iso", "isolated-20260916T064442079079Z")]


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> None:
    queries = [q["query_id"] for q in read_jsonl(ROOT / "queries.jsonl")]
    qset = set(queries)

    # ---- sources of positives -----------------------------------------------------------
    seed = {}
    for row in read_jsonl(ROOT / "seed_judgments.jsonl"):
        seed[(row["query_id"], row["doc_id"])] = int(row.get("relevance", 0))

    existing = {}
    for line in (ROOT / "qrels.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            qid, did, rel = line.split("\t")
            existing[(qid, did)] = int(rel)
    for path in sorted((ROOT / "judgments").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            existing[(path.stem, row["doc_id"])] = int(row["relevance"])

    seed_pos = {k for k, v in seed.items() if v >= 2}
    existing_pos = {k for k, v in existing.items() if v >= 2}
    union_pos = seed_pos | existing_pos

    # ---- scored Top-K --------------------------------------------------------------------
    scored: dict[str, set] = defaultdict(set)
    lengths = Counter()
    run_stats = {}
    for run_label, dirname in RUNS:
        planned = ok = failed = warmup = 0
        codes = Counter()
        for row in read_jsonl(ROOT / "results" / dirname / "runs.jsonl"):
            if row.get("phase") == "warmup":
                warmup += 1
                continue
            planned += 1
            if row.get("status") == "ok":
                ok += 1
                lengths[len(row.get("results") or [])] += 1
                for item in row.get("results") or []:
                    scored[row["query_id"]].add(item["doc_id"])
            else:
                failed += 1
                codes[row.get("status")] += 1
        run_stats[run_label] = {"planned_formal": planned, "ok": ok, "failed": failed,
                                "warmup": warmup, "failure_codes": dict(codes)}
        # iso run carries the same keys

    in_topk = {p for p in union_pos if p[1] in scored.get(p[0], set())}
    out_topk = union_pos - in_topk

    print("=" * 66)
    print("1. 已知正例的三个集合")
    print(f"  种子正例 (seed_judgments, >=2)        : {len(seed_pos)}")
    print(f"  已有正例 (qrels + judgments, >=2)     : {len(existing_pos)}")
    print(f"  两者并集                              : {len(union_pos)}")
    print(f"    并集中 落在实际评分 Top-{TOP_K} 内   : {len(in_topk)}")
    print(f"    并集中 不在 Top-{TOP_K} 内            : {len(out_topk)}  <- 保留为待处理，不丢弃")
    print(f"  仅种子独有 / 仅已有独有               : "
          f"{len(seed_pos - existing_pos)} / {len(existing_pos - seed_pos)}")
    print(f"  涉及题目数                            : {len({p[0] for p in union_pos})}")
    uncovered = sorted(qset - {p[0] for p in union_pos})
    print(f"  无任何已知正例的题目                  : {len(uncovered)} -> {uncovered[:8]}")
    print()
    print("  按等级分布（并集内）:")
    lvl = Counter()
    for p in union_pos:
        lvl[seed.get(p) if p in seed_pos else existing.get(p)] += 1
    print(f"    {dict(sorted((k, v) for k, v in lvl.items() if k))}")
    print()

    print("=" * 66)
    print("2. 全部计划运行清单（失败不得从分母消失）")
    total_planned = total_ok = total_failed = 0
    for label, s in run_stats.items():
        total_planned += s["planned_formal"]
        total_ok += s["ok"]
        total_failed += s["failed"]
        rate = s["failed"] / s["planned_formal"] * 100 if s["planned_formal"] else 0
        print(f"  {label:<5} 计划(正式) {s['planned_formal']:>4} | ok {s['ok']:>4} | "
              f"失败 {s['failed']:>3} | 预热 {s['warmup']:>3} | 失败率 {rate:.1f}% "
              f"{s['failure_codes'] if s['failure_codes'] else ''}")
    rate = total_failed / total_planned * 100 if total_planned else 0
    print(f"  合计      计划 {total_planned} | ok {total_ok} | 失败 {total_failed} | 失败率 {rate:.1f}%")
    print()

    print("=" * 66)
    print(f"3. 成功运行的返回条数分布（TOP_K={TOP_K}，不足不补齐）")
    for n, c in sorted(lengths.items()):
        flag = "  <- 少于 20，按实际条数保留；Precision@20 分母仍为 20" if n < TOP_K else ""
        print(f"  返回 {n:>3} 条: {c} 次{flag}")
    print(f"  共 {sum(lengths.values())} 次成功运行")


if __name__ == "__main__":
    main()
