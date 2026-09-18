"""Self-check for verify_judgments.py against tiny synthetic cases.

Covers exactly the five defects the audit named, so the fix is demonstrable without touching the
real annotations:

  1. an L3 (glyph-folded) quote must NOT count as a pass
  2. statistics must separate failed RECORDS from failed QUOTES
  3. type validation must reject a bare string and a bool
  4. id set problems must reach affected_records
  5. a missing output file must not be silently skipped

Run: python -B tests/biomass_furan/selfcheck_verify.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_judgments as V  # noqa: E402

FAILURES: list[str] = []

# Deliberately uses U+25E6 in the source and U+00B0 in the quote.
SOURCE = "The furfural yield was 130 \u25e6C at 1 h."
CORPUS = {"chk_a": SOURCE}
POOL = ["chk_a"]


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"   <- {detail!r}"))
    if not ok:
        FAILURES.append(name)


def run(rows: list, pool: list[str] = POOL, corpus: dict = CORPUS, qid: str = "q_test") -> dict:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "out.json"
        path.write_text(json.dumps({"query_id": qid, "judgments": rows}, ensure_ascii=False),
                        encoding="utf-8")
        return V.verify_one(qid, pool, corpus, path)


print("1) L3 字形折叠引文不得算作通过")
r = run([{"doc_id": "chk_a", "relevance": 3, "reason": "",
          "evidence_quotes": ["The furfural yield was 130 \u00b0C at 1 h."]}])
check("status == review（不是 ok）", r["status"] == "review", r["status"])
check("review_records == 1", r["review_records"] == 1, r["review_records"])
check("未计入 affected_records", r["affected_records"] == 0, r["affected_records"])

print("\n2) 失败记录数与失败引文数分开统计")
r = run([{"doc_id": "chk_a", "relevance": 2, "reason": "",
          "evidence_quotes": ["NOT IN SOURCE ONE", "NOT IN SOURCE TWO"]}])
check("positive_unmatched_records == 1", r["positive_unmatched_records"] == 1,
      r["positive_unmatched_records"])
check("unmatched_quotes == 2", r["unmatched_quotes"] == 2, r["unmatched_quotes"])

print("\n3) 字段类型校验")
r = run([{"doc_id": "chk_a", "relevance": 2, "reason": "", "evidence_quotes": "BAC"}])
check("evidence_quotes 为字符串 -> issues",
      r["status"] == "issues" and any(i["kind"] == "evidence_quotes_not_string_list"
                                      for i in r["issues"]), [i["kind"] for i in r["issues"]])
r = run([{"doc_id": "chk_a", "relevance": True, "reason": "",
          "evidence_quotes": [SOURCE]}])
check("relevance=True -> issues",
      r["status"] == "issues" and any(i["kind"] == "relevance_invalid" for i in r["issues"]),
      [i["kind"] for i in r["issues"]])
r = run([{"doc_id": "chk_a", "relevance": 0, "evidence_quotes": [], "reason": ""}])
check("0 级缺 reason -> issues",
      any(i["kind"] == "missing_reason" for i in r["issues"]), [i["kind"] for i in r["issues"]])

print("\n4) id 集合问题要进入 affected_records")
rows = [{"doc_id": "chk_a", "relevance": 0, "evidence_quotes": [], "reason": "x"},
        {"doc_id": "chk_a", "relevance": 0, "evidence_quotes": [], "reason": "y"}]
r = run(rows)
check("重复 ID -> issues", any(i["kind"] == "duplicate_id" for i in r["issues"]),
      [i["kind"] for i in r["issues"]])
check("受影响记录 >= 1", r["affected_records"] >= 1, r["affected_records"])
r = run([{"doc_id": "chk_ghost", "relevance": 0, "evidence_quotes": [], "reason": "x"}])
check("多余 ID -> issues", any(i["kind"] == "extra_id" for i in r["issues"]),
      [i["kind"] for i in r["issues"]])
check("多余 ID 计入 affected", r["affected_records"] >= 1, r["affected_records"])

print("\n5) 无产出不得静默跳过")
r = V.verify_one("q_missing", POOL, CORPUS, Path("definitely/not/here.json"))
check("status == absent", r["status"] == "absent", r["status"])

print()
if FAILURES:
    print(f"失败 {len(FAILURES)} 项: {FAILURES}")
    raise SystemExit(1)
print("全部通过")
