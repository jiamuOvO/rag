"""Merge targeted re-judgements back into the judgment files, with a full before/after log.

For each retask/<qid>.result.json:
  * <removed> ids are dropped
  * every re-judged record REPLACES all prior rows for that doc_id (handles the duplicate case),
    or is APPENDED when the block had no prior record

Rows are then re-sorted into pool order so the file mirrors the task file. Each change is logged
with rules_version, so the provenance of a record is always reconstructable.

Usage: python -B tests/biomass_furan/apply_retask.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES_VERSION = "v2"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> None:
    pool_order = {p["query_id"]: [e["doc_id"] for e in p["pool"]] for p in read_jsonl(ROOT / "pool.jsonl")}
    log: list[dict] = []

    for result_path in sorted((ROOT / "retask").glob("*.result.json")):
        qid = result_path.name.replace(".result.json", "")
        target = ROOT / "judgments" / f"{qid}.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        rows: list[dict] = payload["judgments"]
        result = json.loads(result_path.read_text(encoding="utf-8"))

        for doc_id in result.get("removed") or []:
            before = [r for r in rows if r.get("doc_id") == doc_id]
            rows = [r for r in rows if r.get("doc_id") != doc_id]
            log.append({"query_id": qid, "action": "remove", "doc_id": doc_id,
                        "rules_version": RULES_VERSION, "before": before, "after": None})

        for new_row in result["judgments"]:
            doc_id = new_row["doc_id"]
            before = [r for r in rows if r.get("doc_id") == doc_id]
            rows = [r for r in rows if r.get("doc_id") != doc_id]
            rows.append(new_row)
            log.append({"query_id": qid,
                        "action": "replace" if before else "append",
                        "doc_id": doc_id, "rules_version": RULES_VERSION,
                        "before": before, "after": new_row})

        order = pool_order.get(qid, [])
        index = {d: i for i, d in enumerate(order)}
        rows.sort(key=lambda r: index.get(r.get("doc_id"), 10 ** 6))
        payload["judgments"] = rows
        payload["rules_version"] = RULES_VERSION
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {qid}: 变更 {sum(1 for e in log if e['query_id'] == qid)} 项")

    (ROOT / "retask_merge_log.json").write_text(
        json.dumps({"rules_version": RULES_VERSION, "changes": log}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n合计 {len(log)} 项变更 -> retask_merge_log.json")


if __name__ == "__main__":
    main()
