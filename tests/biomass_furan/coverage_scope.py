"""Compute the first-release scoring coverage set, and how many judgements it still needs.

Coverage set for a question (authoritative scope for the first release):

    union over ALL judging runs x ALL repeats x ALL retrievers of candidates with rank <= TOP_K
  U known seed positives
  U existing judged positives (relevance >= 2)

Every repeat is included on purpose -- picking only the best repeat would hide candidates.

Each pair is then classified against the labels already on disk:

    reusable       level 0    -- no rules revision touched the 0 definition
    review_l1      level 1    -- v2 tightened the 1-level boundary
    review_l23     level 2-3  -- v3 clarified the 2/3 split, v4 added the named-source rule
    unlabelled     no label anywhere

A different rules FILE version is not by itself a reason to redo a label; the classification is
by the rule that actually changed.

Usage: python -B tests/biomass_furan/coverage_scope.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOP_K = 20
# The released ranking (what actually got scored) lives in runs.jsonl -- stages.jsonl only logs
# the per-leg candidates. Both runs are included; every repeat and both phases are kept apart.
RUNS = [
    ("http", "http-20260916T063408115501Z"),
    ("iso", "isolated-20260916T064442079079Z"),
]


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> None:
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c["text"] for c in read_jsonl(ROOT / "corpus.jsonl")}

    # ---- 1. coverage = union of the SCORED Top-K, every run x repeat, plus per-leg reference --
    coverage: dict[str, set] = defaultdict(set)
    repeats_seen: dict[str, set] = defaultdict(set)
    per_run: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    per_leg: dict[str, set] = defaultdict(set)

    for run_label, dirname in RUNS:
        for row in read_jsonl(ROOT / "results" / dirname / "runs.jsonl"):
            if row.get("phase") != "formal" or row.get("status") != "ok":
                continue
            qid = row["query_id"]
            repeats_seen[qid].add(row.get("repeat"))
            for item in row.get("results") or []:
                coverage[qid].add(item["doc_id"])
                per_run[run_label][qid].add(item["doc_id"])
        for row in read_jsonl(ROOT / "results" / dirname / "stages.jsonl"):
            for cand in row.get("candidates", []):
                if int(cand.get("rank", 10 ** 6)) <= TOP_K:
                    per_leg[row["query_id"]].add(cand["doc_id"])

    # ---- 2. seed positives and existing judged positives ---------------------------------
    seed_pos: dict[str, set] = defaultdict(set)
    for row in read_jsonl(ROOT / "seed_judgments.jsonl"):
        if int(row.get("relevance", 0)) >= 2:
            seed_pos[row["query_id"]].add(row["doc_id"])

    labels: dict[str, dict[str, int]] = defaultdict(dict)
    for line in (ROOT / "qrels.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            qid, did, rel = line.split("\t")
            labels[qid][did] = int(rel)
    for path in sorted((ROOT / "judgments").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[path.stem][row["doc_id"]] = int(row["relevance"])

    # A record counts as reusable ONLY if it passes mechanical checks: legal level, a verbatim
    # quote for level 2/3, a reason for level 0/1. "Appeared in gapfill" is NOT sufficient --
    # a failed or conflicting record goes back to the pending list.
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from verify_judgments import match_level

    judged_under_current_rules: set[tuple[str, str]] = set()
    rejected_records: list[dict] = []
    for path in sorted((ROOT / "gapfill").glob("*/batch_*.json")):
        qid = path.parent.name
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            did, rel = row.get("doc_id"), row.get("relevance")
            if isinstance(rel, int) and not isinstance(rel, bool):
                labels[qid][did] = rel
            ok = (type(rel) is int and rel in (0, 1, 2, 3)
                  and isinstance(did, str) and bool(did))
            if ok and rel >= 2:
                quotes = row.get("evidence_quotes") or []
                ok = isinstance(quotes, list) and any(
                    isinstance(q, str) and q.strip() for q in quotes)
                if ok:
                    for q in quotes:
                        lvl, _ = match_level(q, corpus.get(did, ""))
                        if lvl >= 3:          # L3 待核验 / L4 失败 -> 不可复用
                            ok = False
                            break
            elif ok:
                ok = isinstance(row.get("reason"), str) and bool(row["reason"].strip())
            if ok:
                judged_under_current_rules.add((qid, did))
            else:
                rejected_records.append({"query_id": qid, "doc_id": did, "relevance": rel})

    # ---- 3. assemble and classify --------------------------------------------------------
    # The first release scope is FROZEN. A re-check that downgrades a positive must not evict that
    # pair from the annotation scope it was already committed to -- the scope is the list of pairs
    # the release promised to judge, not a running function of whatever the positive set is today.
    # frozen_scope_1312.json is the authoritative list; pairs that only qualify now are reported
    # as a supplement and are NOT silently merged into the release scope.
    supplement = []
    frozen = ROOT / "frozen_scope_1312.json"
    if frozen.exists():
        scope_by_q = defaultdict(set)
        for line in json.loads(frozen.read_text(encoding="utf-8")):
            qid, did = line.split("\t")
            scope_by_q[qid].add(did)
        for qid in sorted(queries):
            live = set(coverage.get(qid, set())) | seed_pos.get(qid, set())
            live |= {d for d, r in labels.get(qid, {}).items() if r >= 2}
            supplement += [(qid, d) for d in live - scope_by_q.get(qid, set())]
        scope_source = "frozen_scope_1312.json（固定）"
    else:
        scope_by_q = {qid: (set(coverage.get(qid, set())) | seed_pos.get(qid, set())
                            | {d for d, r in labels.get(qid, {}).items() if r >= 2})
                      for qid in sorted(queries)}
        scope_source = "按当前标签实时计算（无固定清单）"

    buckets = defaultdict(int)
    per_query = []
    for qid in sorted(queries):
        scope = scope_by_q.get(qid, set())
        counts = defaultdict(int)
        for did in scope:
            rel = labels.get(qid, {}).get(did)
            if rel is None:
                counts["unlabelled"] += 1
            elif (qid, did) in judged_under_current_rules:
                counts["reusable"] += 1          # 已按当前规范判定，无需再动
            elif rel == 0:
                counts["reusable"] += 1          # 0 级定义三个版本都未改
            elif rel == 1:
                counts["review_l1"] += 1
            else:
                counts["review_l23"] += 1
        for k, v in counts.items():
            buckets[k] += v
        per_query.append({
            "query_id": qid, "scope": len(scope),
            "scored_topk": len(coverage.get(qid, set())),
            "per_leg_topk_reference": len(per_leg.get(qid, set())),
            "seed_positives": len(seed_pos.get(qid, set())),
            "repeats": sorted(x for x in repeats_seen.get(qid, set()) if x is not None),
            "named_source": bool(queries[qid].get("source_group")),
            **{k: counts.get(k, 0) for k in ("reusable", "review_l1", "review_l23", "unlabelled")},
        })

    scope_total = sum(p["scope"] for p in per_query)
    print(f"首版候选总数: {scope_total}｜口径来源: {scope_source}")
    print(f"  其中 实际评分 Top-{TOP_K}       : {sum(p['scored_topk'] for p in per_query)}")
    print(f"  （参考）各腿 Top-{TOP_K} 并集   : {sum(p['per_leg_topk_reference'] for p in per_query)}")
    print(f"  其中种子正例               : {sum(p['seed_positives'] for p in per_query)}")
    supplement = sorted(set(supplement))
    print(f"  增补候选（仅当前才够正例条件，未并入首版范围）: {len(supplement)} 条")
    print()
    print("按已有标签分类:")
    print(f"  可复用（0 级，规则未变）      : {buckets['reusable']}")
    print(f"  需复核（1 级，受 v2 影响）    : {buckets['review_l1']}")
    print(f"  需复核（2/3 级，受 v3/v4 影响）: {buckets['review_l23']}")
    print(f"  新增待标（完全无标签）        : {buckets['unlabelled']}")
    print()
    print(f"需复核合计: {buckets['review_l1'] + buckets['review_l23']}")
    print(f"验收缺口（需复核 + 待标）: {buckets['review_l1'] + buckets['review_l23'] + buckets['unlabelled']}")
    print()

    total_gap = len(judged_under_current_rules) + len(rejected_records)
    print(f"gapfill 记录 {total_gap} 条 | 通过校验、计入可复用 {len(judged_under_current_rules)} | "
          f"未通过、退回待处理 {len(rejected_records)}")
    for r in rejected_records:
        print(f"    未通过: {r['query_id']} {str(r['doc_id'])[:26]} 等级={r['relevance']}")
    print()
    named = [p for p in per_query if p["named_source"]]
    print(f"点名了来源的题目: {len(named)} 题（v4 规则适用，其正例需按来源核对）")
    bad = [p for p in per_query if p["unlabelled"] == p["scope"] and p["scope"]]
    print(f"完全没有标签的题目: {len(bad)} 题 -> {[p['query_id'] for p in bad][:10]}")
    print()
    print("每题明细（前 12 题）:")
    print(f"  {'题目':<8}{'范围':>5}{'可复用':>7}{'复核1':>6}{'复核23':>7}{'待标':>6}{'点名来源':>9}")
    for p in per_query[:12]:
        print(f"  {p['query_id']:<8}{p['scope']:>5}{p['reusable']:>7}{p['review_l1']:>6}"
              f"{p['review_l23']:>7}{p['unlabelled']:>6}{'是' if p['named_source'] else '':>9}")

    out = ROOT / "coverage_scope.json"
    out.write_text(json.dumps({
        "top_k": TOP_K, "runs": [r[0] for r in RUNS],
        "scope_source": scope_source,
        "totals": {"scope": scope_total, **{k: buckets[k] for k in
                                            ("reusable", "review_l1", "review_l23", "unlabelled")}},
        "scope_supplement_count": len(supplement),
        "per_query": per_query,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n写出 {out}")


if __name__ == "__main__":
    main()
