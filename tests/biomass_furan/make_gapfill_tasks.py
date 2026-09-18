"""Build gap-fill batches: 5 blocks each, mixing new judgements and positive re-checks.

Ordering inside a question: positive re-checks (level 2/3) first, then level-1 re-checks, then
unlabelled blocks. That keeps the first batches from being all-easy level-0 work, which would
make throughput look better than it is.

Re-checks carry the PRIOR judgement and its quotes so the annotator can confirm or correct,
rather than starting blind.

Usage:
    python -B tests/biomass_furan/make_gapfill_tasks.py --limit 10
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gapfill"
BATCH = 5
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
    limit_batches = None
    if "--limit" in __import__("sys").argv:
        limit_batches = int(__import__("sys").argv[__import__("sys").argv.index("--limit") + 1])

    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    scope = json.loads((ROOT / "coverage_scope.json").read_text(encoding="utf-8"))

    labels: dict[str, dict[str, int]] = defaultdict(dict)
    for line in (ROOT / "qrels.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            qid, did, rel = line.split("\t")
            labels[qid][did] = int(rel)
    prior: dict[tuple[str, str], dict] = {}
    for path in sorted((ROOT / "judgments").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[path.stem][row["doc_id"]] = int(row["relevance"])
            prior[(path.stem, row["doc_id"])] = row

    # scope membership per question, taken from coverage_scope.json
    scope_docs: dict[str, list[str]] = {}
    pool = {p["query_id"]: [e["doc_id"] for e in p["pool"]] for p in read_jsonl(ROOT / "pool.jsonl")}
    scored = {p["query_id"] for p in scope["per_query"]}
    del scored
    for entry in scope["per_query"]:
        qid = entry["query_id"]
        scope_docs[qid] = [d for d in pool.get(qid, []) if True]

    # rebuild the authoritative scope the same way coverage_scope.py did
    import subprocess  # noqa: F401  (kept explicit: scope comes from coverage_scope.json)
    cov = json.loads((ROOT / "coverage_scope.json").read_text(encoding="utf-8"))
    del scope_docs
    scope_docs = {}
    for entry in cov["per_query"]:
        scope_docs[entry["query_id"]] = entry

    # a question's blocks = anything in its scope, ordered by need
    RUNS = ["http-20260916T063408115501Z", "isolated-20260916T064442079079Z"]
    topk: dict[str, list[str]] = defaultdict(list)
    seen = defaultdict(set)
    for dirname in RUNS:
        for row in read_jsonl(ROOT / "results" / dirname / "runs.jsonl"):
            if row.get("phase") != "formal" or row.get("status") != "ok":
                continue
            for item in row.get("results") or []:
                if item["doc_id"] not in seen[row["query_id"]]:
                    seen[row["query_id"]].add(item["doc_id"])
                    topk[row["query_id"]].append(item["doc_id"])
    seed_pos = defaultdict(set)
    for row in read_jsonl(ROOT / "seed_judgments.jsonl"):
        if int(row.get("relevance", 0)) >= 2:
            seed_pos[row["query_id"]].add(row["doc_id"])

    OUT.mkdir(exist_ok=True)
    # Collect every question's gap first, then interleave so the early batches mix positive
    # re-checks with genuinely new judgements -- otherwise throughput would be measured on
    # re-checks alone and would not predict the cost of the 866 unlabelled blocks.
    per_q: dict[str, list[str]] = {}
    for qid in sorted(queries):
        need = set(topk.get(qid, [])) | seed_pos.get(qid, set())
        need |= {d for d, r in labels.get(qid, {}).items() if r >= 2}
        recheck = [d for d in need if (labels.get(qid, {}).get(d) or 0) >= 2]   # 正例复核优先
        recheck1 = [d for d in need if labels.get(qid, {}).get(d) == 1]         # 1 级复核
        todo = [d for d in need if labels.get(qid, {}).get(d) is None]          # 待标
        if recheck or recheck1 or todo:
            per_q[qid] = recheck + recheck1 + todo
    has_recheck = [q for q, v in per_q.items() if any((labels.get(q, {}).get(d) or 0) >= 2 for d in v)]
    only_new = [q for q, v in per_q.items() if all(labels.get(q, {}).get(d) is None for d in v)]
    order, i, j = [], 0, 0
    while i < len(has_recheck) or j < len(only_new):
        if i < len(has_recheck):
            order.append(has_recheck[i]); i += 1
        if j < len(only_new):
            order.append(only_new[j]); j += 1
    order += [q for q in per_q if q not in order]

    made = 0
    index = []
    for qid in order:
        ordered = per_q[qid]
        if not ordered:
            continue
        out_dir = OUT / qid
        out_dir.mkdir(parents=True, exist_ok=True)
        for n, start in enumerate(range(0, len(ordered), BATCH), start=1):
            if limit_batches is not None and made >= limit_batches:
                break
            chunk = ordered[start:start + BATCH]
            lines = [f"题目编号: {qid}", f"问题: {queries[qid]['question']}",
                     f"类型: {queries[qid]['question_type']} | 难度: {queries[qid]['difficulty']}",
                     "", "必须覆盖的事实:"]
            for fact in queries[qid].get("required_facts") or []:
                lines.append(f"  - {fact}")
            lines += ["", f"参考答案: {queries[qid].get('reference_answer') or '(无)'}"]
            if queries[qid].get("source_group"):
                lines += ["", f"题目指定来源: {queries[qid]['source_group']}"]
            lines += ["", "=" * 64, f"本批 {len(chunk)} 个块。逐个给出 0/1/2/3；复核项给出你的最终等级。",
                      "标了【原判定】的块是复核项：可以维持、升级或降级，但必须依据正文说明理由。",
                      "=" * 64, ""]
            for j, did in enumerate(chunk, start=1):
                doc = corpus[did]
                kind = "待标" if labels.get(qid, {}).get(did) is None else "复核"
                lines.append(f"[{j}] doc_id={did} | {kind} | 来源={doc['source_id']} | 标题={doc['title']}")
                old = prior.get((qid, did))
                if old:
                    q = (old.get("evidence_quotes") or [""])[0]
                    lines.append(f"    【原判定】等级={old.get('relevance')}｜引文={q[:150]}")
                    lines.append(f"    【原理由】{str(old.get('reason') or '')[:150]}")
                lines.append(wrap(doc["text"]))
                lines.append("")
            (out_dir / f"batch_{n:02d}.txt").write_text("\n".join(lines), encoding="utf-8")
            index.append({"query_id": qid, "batch": n, "blocks": len(chunk),
                          "recheck_pos": sum(1 for d in chunk if (labels.get(qid, {}).get(d) or 0) >= 2),
                          "recheck_l1": sum(1 for d in chunk if labels.get(qid, {}).get(d) == 1),
                          "new": sum(1 for d in chunk if labels.get(qid, {}).get(d) is None),
                          "docs": chunk})
            made += 1
            if limit_batches is not None and made >= limit_batches:
                break

    (OUT / "_gapfill_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    tot = sum(i["blocks"] for i in index)
    print(f"生成 {made} 批 / {tot} 块 -> {OUT}")
    print(f"  其中 正例复核 {sum(i['recheck_pos'] for i in index)} | "
          f"1级复核 {sum(i['recheck_l1'] for i in index)} | 待标 {sum(i['new'] for i in index)}")


if __name__ == "__main__":
    main()
