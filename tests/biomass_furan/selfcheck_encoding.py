"""Self-check for the judgment encodings and the request-budget policy. No API calls, no cost.

Fails if the compact and per-block paths ever disagree about the annotation rules, or if the
two retry layers could ever be composed without the shared cap. Run:
    python -B tests/biomass_furan/selfcheck_encoding.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import annotate  # noqa: E402
import run_full_gapfill as runner  # noqa: E402

CHECKS = 0


def ok(condition: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError("FAIL: " + label)
    print("  ok  " + label)


def raises(fn, label: str) -> None:
    try:
        fn()
    except (ValueError, KeyError, TypeError, AttributeError):
        ok(True, label)
    else:
        ok(False, label)


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    queries = [next(read_jsonl(ROOT / "queries.jsonl"))]
    docs = []
    for d in read_jsonl(ROOT / "corpus.jsonl"):
        if len(d["text"]) > 400:
            docs.append(d)
        if len(docs) == 3:
            break
    qid = queries[0]["query_id"]
    d0, d1, d2 = (d["doc_id"] for d in docs)
    quote = {d["doc_id"]: " ".join(d["text"].split())[:120] for d in docs}

    print("1) prompt files carry the same annotation rules")
    ok(set(annotate.PROMPT_FILES) == {"compact", "perblock"}, "two encodings registered")
    compact_prompt = annotate.PROMPT_FILES["compact"].read_text(encoding="utf-8")
    block_prompt = annotate.PROMPT_FILES["perblock"].read_text(encoding="utf-8")
    for rule in ("L1a", "L1b", "L1c", "L1d"):
        ok(rule in block_prompt, f"per-block prompt carries {rule}")
    ok("proofs" in compact_prompt and "proofs" not in block_prompt, "the two specs stay distinct")
    for term in ("zero_reasons", "exact contiguous", "never paraphrase"):
        ok(term.lower() in compact_prompt.lower() and term.lower() in block_prompt.lower(),
           f"both specs state: {term}")

    print("2) per-block schema")
    block_no_zero = {"grades": {qid: [2, 3, 2]}, "zero_reasons": {},
                     "evidence": {qid: {did: {"quote": quote[did], "reason": "L1c comparable run",
                                              "fact_ids": []} for did in (d0, d1, d2)}}}
    rows = annotate.parse_response(json.dumps(block_no_zero), queries, docs)
    ok([r["relevance"] for r in rows] == [2, 3, 2], "per-block all-nonzero grades parse")
    ok(all(r["evidence_quotes"] == [quote[r["doc_id"]]] for r in rows),
       "per-block quotes carried per document")

    print("3) a rationale is required exactly when the question has a zero-grade document")
    block_with_zero_no_reason = {"grades": {qid: [0, 3, 2]}, "zero_reasons": {},
                                 "evidence": {qid: {d: {"quote": quote[d], "reason": "r",
                                                        "fact_ids": []} for d in (d1, d2)}}}
    raises(lambda: annotate.parse_response(json.dumps(block_with_zero_no_reason), queries, docs),
           "per-block: zero present + no rationale -> rejected")
    block_with_zero_ok = json.loads(json.dumps(block_with_zero_no_reason))
    block_with_zero_ok["zero_reasons"] = {qid: "doc0: other paper, unrelated"}
    rows = annotate.parse_response(json.dumps(block_with_zero_ok), queries, docs)
    ok([r["relevance"] for r in rows] == [0, 3, 2], "per-block: zero present + rationale -> accepted")
    ok(rows[0]["reason"] == "doc0: other paper, unrelated", "zero row carries the query rationale")

    ok(annotate.parse_compact(
        json.dumps({"grades": ["232"], "zero_reasons": [],
                    "proofs": [{"document": i, "queries": [0], "quote": quote[d], "reason": "r"}
                               for i, d in enumerate((d0, d1, d2))]}),
        queries, docs)[0]["relevance"] == 2,
       "compact: all-nonzero + empty zero_reasons -> accepted (was a false failure)")
    raises(lambda: annotate.parse_compact(
        json.dumps({"grades": ["032"], "zero_reasons": [],
                    "proofs": [{"document": 1, "queries": [0], "quote": quote[d1], "reason": "a"},
                               {"document": 2, "queries": [0], "quote": quote[d2], "reason": "b"}]}),
        queries, docs),
        "compact: zero present + empty zero_reasons -> rejected")
    rows = annotate.parse_compact(
        json.dumps({"grades": ["032"], "zero_reasons": ["doc0: unrelated"],
                    "proofs": [{"document": 1, "queries": [0], "quote": quote[d1], "reason": "a"},
                               {"document": 2, "queries": [0], "quote": quote[d2], "reason": "b"}]}),
        queries, docs)
    ok([r["relevance"] for r in rows] == [0, 3, 2], "compact: zero present + rationale -> accepted")
    ok(rows[0]["reason"] == "doc0: unrelated", "compact zero row carries the query rationale")
    raises(lambda: annotate.parse_compact(
        json.dumps({"grades": ["232"], "zero_reasons": ["a", "b"], "proofs": []}), queries, docs),
        "compact: more rationales than questions -> rejected")

    print("4) neither schema silently satisfies the other")
    raises(lambda: annotate.parse_compact(json.dumps(block_no_zero), queries, docs),
           "parse_compact rejects the per-block schema")
    raises(lambda: annotate.parse_response(
        json.dumps({"grades": {qid: "232"}, "zero_reasons": {}, "evidence": {qid: {}}}), queries, docs),
        "parse_response rejects the compact schema")

    print("5) a rejected response still carries its billed usage")
    exc = annotate._rejected(ValueError("boom"), {"prompt_tokens": 11, "total_tokens": 20}, "abc123")
    ok(getattr(exc, "usage", None) == {"prompt_tokens": 11, "total_tokens": 20},
       "usage is attached to the rejected exception")
    ok(getattr(exc, "response_id", None) == "abc123", "response id is attached for the audit trail")

    print("6) the two retry layers cannot multiply")
    composed = (1 + runner.NETWORK_EXTRA_ATTEMPTS) * (1 + runner.ENCODING_SWITCHES)
    ok(runner.MAX_REQUESTS_PER_BATCH < composed,
       f"shared cap {runner.MAX_REQUESTS_PER_BATCH} is tighter than the composed worst case {composed}")
    ok(runner.ENCODING_SWITCHES == 1, "at most one encoding switch per batch")
    ok(runner.MAX_REQUESTS_PER_BATCH >= 2, "a batch may still retry a transport error")

    print(f"\n{CHECKS}/{CHECKS} 断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
