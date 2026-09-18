"""Merge validated gapfill judgements into the formal label store, by (query_id, doc_id).

This is the step that turns the staging area into frozen labels, so its rules are explicit:

  * the unit is the PAIR (query_id, doc_id), never the batch. The earliest 29 records were
    produced under a different partition of the same blocks, so batch identity is not comparable.
  * a pair already present in judgments/<qid>.json is KEPT. Only a graded disagreement is
    reported, in merge_conflicts.json, for adjudication. Existing corrections are never overwritten.
  * EXCEPTION -- re-check items. make_gapfill_tasks.py only puts a pair into a batch when the
    question needs it re-reviewed, and it prints 【原判定】/【原理由】 into the task file with the
    instruction "标了【原判定】的块是复核项：可以维持、升级或降级". For those pairs the gapfill
    result IS the final grade, so it supersedes the old value. Every supersession is logged with
    before/after in merge_revisions.json. Use --keep-existing to invert this and keep the old value.
  * a pair with no explicit judgement is never written. There is no implicit zero anywhere: a
    missing pair simply has no row in qrels.tsv.
  * every judgement written carries its provenance fields (method / model / review_status /
    raw_response_id where the record has them).
  * judgments/ is backed up before the first write, and the whole run is idempotent.

Usage:
    python -B tests/biomass_furan/merge_gapfill.py --dry-run
    python -B tests/biomass_furan/merge_gapfill.py --apply
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JUDGES = ROOT / "judgments"
QRELS = ROOT / "qrels.tsv"
CONFLICTS = ROOT / "merge_conflicts.json"
REVISIONS = ROOT / "merge_revisions.json"
REPORT = ROOT / "merge_report.json"

sys.path.insert(0, str(ROOT))
from run_full_gapfill import collect_judged_pairs, read_jsonl  # noqa: E402


def load_existing():
    """Formal labels already on disk: {qid: {doc_id: record}}, preserving record content."""
    existing = defaultdict(dict)
    for path in sorted(JUDGES.glob("*.json")):
        qid = path.stem
        for row in json.loads(path.read_text(encoding="utf-8")).get("judgments") or []:
            if isinstance(row.get("doc_id"), str) and row["doc_id"]:
                existing[qid][row["doc_id"]] = row
    return existing


def write_query(qid, records):
    (JUDGES / f"{qid}.json").write_text(
        json.dumps({"query_id": qid, "judgments": records}, ensure_ascii=False, indent=1),
        encoding="utf-8")


def main() -> int:
    apply_mode = "--apply" in sys.argv
    keep_existing = "--keep-existing" in sys.argv
    queries = [q["query_id"] for q in read_jsonl(ROOT / "queries.jsonl")]
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    existing = load_existing()
    gapfill = collect_judged_pairs(corpus)          # validated pairs only
    # Pairs corrected by the semantic spot-check are protected: a later merge reloads the same
    # gapfill outputs, and must not undo a correction that was made after reading the block.
    protected = set()
    if (ROOT / "semantic_corrections.json").exists():
        for c in json.loads((ROOT / "semantic_corrections.json").read_text(encoding="utf-8")):
            protected.add((c["query_id"], c["doc_id"]))
        print(f"语义抽查修正保护：{len(protected)} 条不会被本次合并改动")
    index_pairs = {(i["query_id"], d) for i in
                   json.loads((ROOT / "gapfill" / "_gapfill_index.json").read_text(encoding="utf-8"))
                   for d in i["docs"]}

    before_pairs = sum(len(v) for v in existing.values())
    print(f"合并前正式标签 {before_pairs} 条 / {len(existing)} 题有文件")
    print(f"gapfill 通过校验的配对 {len(gapfill)} 条")
    print(f"冲突规则：{'保留既有（--keep-existing）' if keep_existing else '复核项以 gapfill 新判定为准，旧值留痕'}")

    added = defaultdict(list)
    revisions = []
    conflicts = []
    unchanged = 0
    for (qid, doc_id), row in sorted(gapfill.items()):
        if qid not in queries:
            continue
        current = existing.get(qid, {}).get(doc_id)
        if (qid, doc_id) in protected and current is not None:
            unchanged += 1
            continue
        if current is None:
            added[qid].append(row)
            continue
        if current.get("relevance") == row.get("relevance"):
            unchanged += 1
            continue
        entry = {"query_id": qid, "doc_id": doc_id,
                 "old": {"relevance": current.get("relevance"),
                         "reason": str(current.get("reason"))[:200],
                         "source": current.get("method") or current.get("review_status") or "existing"},
                 "new": {"relevance": row.get("relevance"),
                         "reason": str(row.get("reason"))[:200],
                         "source": row.get("method") or row.get("source") or "gapfill",
                         "raw_response_id": row.get("raw_response_id")}}
        was_recheck = (qid, doc_id) in index_pairs
        entry["recheck_item"] = was_recheck
        if was_recheck and not keep_existing:
            added[qid].append(row)
            revisions.append(entry)
        else:
            conflicts.append(entry)

    new_total = sum(len(v) for v in added.values())
    print(f"  新增 {new_total} 条（覆盖 {len(added)} 题）｜既有同分不变 {unchanged} 条")
    print(f"  复核项被新判定取代 {len(revisions)} 条（旧值留痕）｜保留既有 {len(conflicts)} 条")
    if revisions:
        up = sum(1 for r in revisions if r["new"]["relevance"] > r["old"]["relevance"])
        print(f"    其中升级 {up} 条、降级 {len(revisions)-up} 条")
        for r in revisions[:5]:
            print(f"    {r['query_id']} {r['doc_id'][:18]}：{r['old']['relevance']} -> {r['new']['relevance']}")

    # The merged store: existing records, with re-check results replacing them in place.
    merged = {}
    for qid in queries:
        bydoc = dict(existing.get(qid, {}))
        for rec in added.get(qid, []):
            bydoc[rec["doc_id"]] = rec
        if bydoc:
            merged[qid] = bydoc

    merged_pairs = {(qid, d) for qid, bydoc in merged.items() for d in bydoc}
    cov = json.loads((ROOT / "coverage_scope.json").read_text(encoding="utf-8"))
    scoped = sum(p["scope"] for p in cov["per_query"])
    print(f"  合并后正式标签 {len(merged_pairs)} 条（首版候选集 {scoped} 组合）")

    if not apply_mode:
        print("\n--dry-run：未写入任何文件")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = ROOT / f"judgments_backup_{stamp}"
    if JUDGES.exists():
        shutil.copytree(JUDGES, backup)
        print(f"\n已备份 judgments/ -> {backup.name}")
    if QRELS.exists():
        shutil.copy2(QRELS, backup / "qrels.tsv")

    for qid, bydoc in merged.items():
        write_query(qid, sorted(bydoc.values(), key=lambda r: r["doc_id"]))

    rows = [(qid, doc_id, rec.get("relevance"))
            for qid, bydoc in merged.items() for doc_id, rec in sorted(bydoc.items())]
    with QRELS.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("query_id\tdoc_id\trelevance\n")
        for qid, doc_id, rel in sorted(rows):
            fh.write(f"{qid}\t{doc_id}\t{rel}\n")

    CONFLICTS.write_text(json.dumps(conflicts, ensure_ascii=False, indent=1), encoding="utf-8")
    # A no-op re-run must not clobber the record of what the first run actually changed.
    if revisions or not REVISIONS.exists():
        REVISIONS.write_text(json.dumps(revisions, ensure_ascii=False, indent=1), encoding="utf-8")
    REPORT.write_text(json.dumps({
        "backup": backup.name, "existing_before": before_pairs, "gapfill_validated": len(gapfill),
        "added": new_total, "queries_extended": len(added), "unchanged": unchanged,
        "revisions_recheck_superseded": len(revisions), "conflicts_kept": len(conflicts),
        "keep_existing_flag": keep_existing,
        "qrels_rows": len(rows), "scope_pairs": scoped,
        "rule": "pair-level; re-check items take the gapfill grade (task files mark them 【原判定】 "
                "and instruct 维持/升级/降级); all other disagreements keep the existing record; "
                "unjudged pairs are never written and never default to 0",
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"已写入 {len(rows)} 行 -> {QRELS.name}")
    print(f"已更新 {len(set(added) | set(existing))} 题的 {JUDGES.name}/<qid>.json")
    print(f"留痕：{CONFLICTS.name} / {REVISIONS.name} / {REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
