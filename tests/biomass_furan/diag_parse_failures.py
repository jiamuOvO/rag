"""Zero-cost diagnosis: replay parse_compact over every saved raw response.

No API calls. Reads runtime/model_responses/*.json, re-runs the same parser the
executor uses, and reports an exact failure taxonomy so direction A/B/C can be
chosen from evidence instead of guesswork.

Usage:  python -B tests/biomass_furan/diag_parse_failures.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import annotate  # noqa: E402


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


BUCKETS = {
    "Compact response omitted query rows": "row_count_mismatch",
    "Compact row has missing/invalid document grades": "grade_digit_count_mismatch",
    "Invalid compact proof indices": "proof_bad_indices",
    "Compact proof quotation not found in original text": "proof_quote_not_found",
    "Missing compact proof reason": "proof_missing_reason",
    "Compact proof conflicts with grade matrix": "proof_conflicts_with_grade",
    "Nonzero compact grades lack proofs": "proof_missing_for_nonzero",
    "Missing compact zero rationale": "zero_reason_missing",
}


def classify(raw: str, docs: list[dict], queries: list[dict]) -> tuple[str, str]:
    """Return (verdict, detail). Delegates to the real parser so the report cannot drift from it."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception as exc:                                  # noqa: BLE001
        balanced = text.count("{") == text.count("}")
        return ("json_invalid_balanced" if balanced else "json_invalid_unbalanced"), str(exc)[:80]

    try:
        annotate.parse_compact(raw, queries, docs)
        return "ok", ""
    except ValueError as exc:
        msg = str(exc)
        bucket = BUCKETS.get(msg, "parse_other")
        detail = msg
        if bucket == "grade_digit_count_mismatch":
            rows = data.get("grades") or []
            for i, row in enumerate(rows):
                if not (isinstance(row, list) and len(row) == len(docs)):
                    detail = f"row {i}: {type(row).__name__} {str(row)[:40]!r} (need {len(docs)} digits)"
                    break
        return bucket, detail


def main() -> int:
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    files = sorted((ROOT / "runtime" / "model_responses").glob("*.json"),
                   key=lambda p: p.stat().st_mtime)

    buckets = Counter()
    per_phase = Counter()
    details: list[tuple[str, str, str]] = []
    scanned = 0

    for path in files:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            continue
        raw = rec.get("response")
        qids = rec.get("query_ids") or []
        dids = rec.get("doc_ids") or []
        if raw is None or not qids or not dids:
            continue
        missing = [q for q in qids if q not in queries] + [d for d in dids if d not in corpus]
        if missing:
            details.append((path.stem[:8], "input_missing", str(missing[:3])))
            continue
        scanned += 1
        qs = [queries[q] for q in qids]
        ds = [corpus[d] for d in dids]
        verdict, detail = classify(raw, ds, qs)
        buckets[verdict] += 1
        per_phase[(rec.get("phase"), verdict)] += 1
        if verdict != "ok":
            details.append((path.stem[:8], verdict, detail))

    print(f"扫描响应 {scanned} 条（共 {len(files)} 个文件）｜成功 {buckets['ok']} ｜失败 {scanned - buckets['ok']}")
    if scanned:
        print(f"失败率 {(scanned - buckets['ok']) / scanned:.1%}")
    print("\n失败分类：")
    for k, v in buckets.most_common():
        print(f"  {k:34s} {v}")
    print("\n按阶段：")
    for (phase, verdict), v in sorted(per_phase.items(), key=lambda x: str(x[0])):
        print(f"  {phase!r:14s} {verdict:34s} {v}")
    if details:
        print("\n明细（前 30 条）：")
        for h, verdict, detail in details[:30]:
            print(f"  {h}  {verdict:32s} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
