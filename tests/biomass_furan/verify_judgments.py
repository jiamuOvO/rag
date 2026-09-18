"""Verify annotation outputs against the pool and the frozen corpus.

MATCH TIERS for every evidence_quote (a quote takes the WORST tier of its fragments):

    L1  literal          -- fragment occurs verbatim in the raw block text
    L2  whitespace-only  -- occurs after collapsing whitespace
    L3  glyph-folded     -- occurs only after applying GLYPH_RULES  -> NEEDS REVIEW, not a pass
    L4  unmatched        -- no tier matches

THREE OUTCOMES per question (see `status`):
    ok      -- issues empty AND review empty
    review  -- only L3 hits; needs human confirmation, NOT a pass
    issues  -- at least one hard defect

STATISTICS keep four units strictly apart:
    records        judgement rows
    positive       rows with relevance >= 2 (the only rows that need quotes)
    quotes         individual evidence_quotes strings
    ids            pool membership problems

  * positive_unmatched_records : positive ROWS containing >= 1 unmatched quote (deduplicated)
  * unmatched_quotes           : unmatched quote STRINGS, can exceed the row count
  * affected_records           : positive rows with a quote problem + rows with a field problem
                                 + rows involved in duplicate / extra / missing ids

Usage:
    python -B tests/biomass_furan/verify_judgments.py --expect q_0001,q_0002
    python -B tests/biomass_furan/verify_judgments.py --expect-all-annotated
    python -B tests/biomass_furan/verify_judgments.py --expect q_0001..q_0016 --json out.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)\s*")

# Heuristic look-alike folding for PDF text layers. The equivalence is CONTEXT-DEPENDENT, so an
# L3 hit is reported for review and never counted as a pass.
GLYPH_RULES: dict[str, str] = {
    "\u25e6": "\u00b0", "\u00ba": "\u00b0", "\u02da": "\u00b0", "\u2218": "\u00b0",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00a0": " ", "\u2009": " ", "\u202f": " ",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
}
GLYPH_FOLD = str.maketrans(GLYPH_RULES)


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def flat(text: str) -> str:
    return " ".join(text.split())


def match_level(quote: str, source_raw: str) -> tuple[int, str]:
    """Return (level, detail). Level 1..4 as documented above."""
    fragments = [f for f in ELLIPSIS.split(quote or "") if f.strip()]
    if not fragments:
        return 4, "empty quote"
    source_flat = flat(source_raw)
    source_folded = source_flat.translate(GLYPH_FOLD)
    worst = 1
    for fragment in fragments:
        if fragment in source_raw:
            tier = 1
        elif flat(fragment) in source_flat:
            tier = 2
        elif flat(fragment).translate(GLYPH_FOLD) in source_folded:
            tier = 3
        else:
            return 4, f"fragment not found: {fragment[:70]!r}"
        worst = max(worst, tier)
    return worst, ""


def verify_one(query_id: str, pool_docs: list[str], corpus: dict[str, str],
               out_path: Path) -> dict:
    issues: list[dict] = []
    review: list[dict] = []
    empty = {"issues": issues, "review": review, "counts": {0: 0, 1: 0, 2: 0, 3: 0},
             "records": 0, "positive_records": 0, "positive_unmatched_records": 0,
             "unmatched_quotes": 0, "review_records": 0, "affected_records": 0,
             "missing_ids": 0, "extra_ids": 0, "duplicate_ids": 0, "pool_size": len(pool_docs)}

    if not out_path.exists():
        return {**empty, "status": "absent", "issues": [{"kind": "output_absent"}]}

    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**empty, "status": "unreadable",
                "issues": [{"kind": "unreadable", "detail": str(exc)}]}

    if not isinstance(payload, dict):
        return {**empty, "status": "malformed", "issues": [{"kind": "payload_not_object"}]}
    if payload.get("query_id") != query_id:
        issues.append({"kind": "query_id_mismatch", "detail": repr(payload.get("query_id"))})

    rows = payload.get("judgments")
    if not isinstance(rows, list):
        return {**empty, "status": "malformed", "issues": [{"kind": "judgments_not_list"}]}

    seen: list[str] = []
    counts = {0: 0, 1: 0, 2: 0, 3: 0}
    affected: set[int] = set()          # row indices
    positive_unmatched: set[int] = set()
    unmatched_quotes = 0
    review_rows: set[int] = set()
    positive_records = 0

    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append({"kind": "row_not_object", "row": idx, "detail": type(row).__name__})
            affected.add(idx)
            continue

        did = row.get("doc_id")
        rel = row.get("relevance")
        quotes = row.get("evidence_quotes", [])
        reason = row.get("reason")

        if not isinstance(did, str) or not did:
            issues.append({"kind": "doc_id_not_string", "row": idx, "detail": repr(did)[:60]})
            affected.add(idx)
        else:
            seen.append(did)

        # `type(rel) is not int` rejects bool, which is an int subclass and would otherwise pass.
        if type(rel) is not int or rel not in (0, 1, 2, 3):
            issues.append({"kind": "relevance_invalid", "row": idx, "doc_id": did,
                           "detail": f"{rel!r} (type {type(rel).__name__})"})
            affected.add(idx)
            continue
        counts[rel] += 1

        if not isinstance(quotes, list) or not all(isinstance(q, str) for q in quotes):
            issues.append({"kind": "evidence_quotes_not_string_list", "row": idx, "doc_id": did,
                           "detail": repr(quotes)[:60]})
            affected.add(idx)
            quotes = []

        if rel >= 2:
            positive_records += 1
            if not any(isinstance(q, str) and q.strip() for q in quotes):
                issues.append({"kind": "positive_without_quote", "doc_id": did, "level": rel,
                               "row": idx})
                affected.add(idx)
                continue
            for q in quotes:
                tier, detail = match_level(q, corpus.get(did, ""))
                if tier == 4:
                    unmatched_quotes += 1
                    positive_unmatched.add(idx)
                    affected.add(idx)
                    issues.append({"kind": "quote_unmatched", "doc_id": did, "level": rel,
                                   "row": idx, "quote": q[:200], "detail": detail})
                elif tier == 3:
                    review_rows.add(idx)
                    review.append({"doc_id": did, "level": rel, "row": idx, "quote": q[:200],
                                   "detail": "matched only after glyph folding"})
        else:
            if not isinstance(reason, str) or not reason.strip():
                issues.append({"kind": "missing_reason", "doc_id": did, "level": rel, "row": idx})
                affected.add(idx)

    pool_set, seen_set = set(pool_docs), set(seen)
    missing = sorted(pool_set - seen_set)
    extra = sorted(seen_set - pool_set)
    duplicates = sorted({d for d in seen if seen.count(d) > 1})
    for did in duplicates + extra:
        for i, r in enumerate(rows):
            if isinstance(r, dict) and r.get("doc_id") == did:
                affected.add(i)
    if duplicates:
        issues.append({"kind": "duplicate_id", "detail": duplicates[:5], "count": len(duplicates)})
    if missing:
        issues.append({"kind": "missing_id", "detail": missing[:5], "count": len(missing)})
    if extra:
        issues.append({"kind": "extra_id", "detail": extra[:5], "count": len(extra)})
    unknown = sorted(seen_set - set(corpus))
    if unknown:
        issues.append({"kind": "id_absent_from_corpus", "detail": unknown[:5], "count": len(unknown)})

    status = "ok" if not issues and not review else ("issues" if issues else "review")
    return {
        "status": status,
        "issues": issues,
        "review": review,
        "counts": counts,
        "records": len(rows),
        "positive_records": positive_records,
        "positive_unmatched_records": len(positive_unmatched),
        "unmatched_quotes": unmatched_quotes,
        "review_records": len(review_rows),
        "affected_records": len(affected),
        "missing_ids": len(missing),
        "extra_ids": len(extra),
        "duplicate_ids": len(duplicates),
        "pool_size": len(pool_docs),
    }


def expand_expect(spec: str) -> list[str]:
    """Accept q_0001,q_0002 or q_0001..q_0016 or a mix."""
    out: list[str] = []
    for token in spec.split(","):
        token = token.strip()
        if ".." in token:
            start, end = token.split("..")
            prefix = start.rstrip("0123456789")
            a, b = int(start[len(prefix):]), int(end[len(prefix):])
            out += [f"{prefix}{i:04d}" for i in range(a, b + 1)]
        elif token:
            out.append(token)
    return out


def main() -> int:
    corpus = {c["doc_id"]: c["text"] for c in read_jsonl(ROOT / "corpus.jsonl")}
    pool = {p["query_id"]: [e["doc_id"] for e in p["pool"]] for p in read_jsonl(ROOT / "pool.jsonl")}

    if "--expect" in sys.argv:
        expect = expand_expect(sys.argv[sys.argv.index("--expect") + 1])
        mode = "指定清单"
    elif "--expect-all-annotated" in sys.argv:
        expect = sorted(p.stem for p in (ROOT / "judgments").glob("*.json"))
        mode = "已产出清单"
    else:
        expect = sorted(pool)
        mode = "全部池内题目"
        print("警告：未指定 --expect，按全部 %d 题检查；构建阶段会有大量 absent。" % len(expect))

    report = {q: verify_one(q, pool.get(q, []), corpus, ROOT / "judgments" / f"{q}.json")
              for q in expect}

    ok = [q for q, r in report.items() if r["status"] == "ok"]
    review = [q for q, r in report.items() if r["status"] == "review"]
    bad = [q for q, r in report.items() if r["status"] in {"issues", "malformed", "unreadable"}]
    absent = [q for q, r in report.items() if r["status"] == "absent"]

    n_rec = sum(r["records"] for r in report.values())
    n_pos = sum(r["positive_records"] for r in report.values())
    n_pos_bad = sum(r["positive_unmatched_records"] for r in report.values())
    n_q_bad = sum(r["unmatched_quotes"] for r in report.values())
    n_rev = sum(r["review_records"] for r in report.values())
    n_aff = sum(r["affected_records"] for r in report.values())

    print(f"检查范围: {mode} | {len(expect)} 题")
    print(f"通过 {len(ok)} | 待核验 {len(review)} | 有问题 {len(bad)} | 无产出 {len(absent)}")
    if absent:
        print(f"  无产出题目: {absent[:8]}{' ...' if len(absent) > 8 else ''}")
    print()
    print("--- 统计（四个单位分开计，不混用）---")
    print(f"  判断记录 records              {n_rec}")
    print(f"  正例记录 positive             {n_pos}")
    print(f"  受影响的判断记录 affected     {n_aff}")
    print(f"  含未匹配引文的正例【记录】    {n_pos_bad}"
          + (f"  = 正例的 {n_pos_bad / n_pos * 100:.2f}%" if n_pos else ""))
    print(f"  未匹配引文【条数】            {n_q_bad}   <- 与上一行单位不同")
    print(f"  L3 待核验【记录】             {n_rev}")
    print(f"  缺失/多余/重复 ID             "
          f"{sum(r['missing_ids'] for r in report.values())}/"
          f"{sum(r['extra_ids'] for r in report.values())}/"
          f"{sum(r['duplicate_ids'] for r in report.values())}")
    print()
    for q, r in report.items():
        c = r["counts"]
        line = (f"  {q}  {r['status']:<9} 记录 {r['records']}/{r['pool_size']}  "
                f"3:{c[3]} 2:{c[2]} 1:{c[1]} 0:{c[0]}")
        if r["review_records"]:
            line += f"  [L3 待核验 {r['review_records']}]"
        print(line)
        for item in r["issues"][:8]:
            print(f"        - {item['kind']}: "
                  f"{json.dumps({k: v for k, v in item.items() if k != 'kind'}, ensure_ascii=False)[:140]}")

    if "--json" in sys.argv:
        i = sys.argv.index("--json")
        dest = Path(sys.argv[i + 1]) if len(sys.argv) > i + 1 else ROOT / "verify_report.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({
            "scope": {"mode": mode, "expected": expect},
            "passed": len(ok), "needs_review": review, "with_issues": bad, "absent": absent,
            "totals": {"records": n_rec, "positive_records": n_pos,
                       "positive_unmatched_records": n_pos_bad, "unmatched_quotes": n_q_bad,
                       "review_records": n_rev, "affected_records": n_aff},
            "glyph_rules": GLYPH_RULES, "detail": report,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n写出 {dest}")

    return 0 if not bad and not absent else 1


if __name__ == "__main__":
    raise SystemExit(main())
