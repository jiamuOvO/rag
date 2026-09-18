"""Triage the batches whose fallback ladder exhausted, and repair only the mechanical defect.

A batch that failed the ladder has NO output file, but its raw responses are on disk in
runtime/model_responses/. This script works from those responses, never from the task files:

  * it never touches gapfill/**/batch_NN.txt (no re-slicing, no renumbering)
  * a batch is only written when the repaired response parses through the REAL parser
  * every write carries a before/after trace into repair_failed_log.json

Only one repair is automated, the same one repair_judgments.py already sanctions:

  R1  glyph-folded quote -> rewritten to the block's exact text, then re-verified

Everything else is left to targeted re-judgement and listed in repair_failed_todo.json:

  * omitted / invented query IDs      -> grades for a question were never produced
  * missing or invalid pair grades    -> same
  * positive evidence coverage mismatch -> a non-zero document has no evidence entry
  * JSON that does not decode         -> no recoverable structure
  * quote matching no fragment (L4)   -> the text is not in the block, not a glyph issue

Usage:
    python -B tests/biomass_furan/repair_failed_batches.py --dry-run
    python -B tests/biomass_furan/repair_failed_batches.py --apply
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "gapfill"
LOG_PATH = ROOT / "repair_failed_log.json"
TODO_PATH = ROOT / "repair_failed_todo.json"
RULES_VERSION = "v2"

sys.path.insert(0, str(ROOT))
import annotate  # noqa: E402
from repair_judgments import restore_quote, verifies  # noqa: E402
from verify_judgments import match_level  # noqa: E402

STRUCTURAL = {
    "Model omitted or invented query IDs": "omitted_or_invented_ids",
    "Missing or invalid pair grades": "missing_or_invalid_grades",
    "Positive evidence coverage mismatch": "missing_evidence_for_nonzero",
    "Missing explicit zero justification": "missing_zero_justification",
    "Missing relevance justification": "missing_evidence_reason",
    "Invented fact ID": "invented_fact_id",
    "Compact response omitted query rows": "omitted_query_rows",
    "Nonzero compact grades lack proofs": "missing_proof_for_nonzero",
    "Missing compact proof reason": "missing_proof_reason",
    "Invalid compact proof indices": "invalid_proof_indices",
    "Compact proof conflicts with grade matrix": "proof_conflicts_with_grade",
    "Missing compact zero rationale": "missing_zero_rationale",
    "Model response truncated or refused": "truncated",
}
QUOTE_ERRORS = {"Compact proof quotation not found in original text",
                "Unsupported model evidence quotation"}


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_inputs():
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    index = json.loads((OUT_DIR / "_gapfill_index.json").read_text(encoding="utf-8"))
    return corpus, queries, index


def responses_for(query_id, doc_ids):
    """Every saved response for this exact (question, block set), newest first."""
    out = []
    for path in (ROOT / "runtime" / "model_responses").glob("*.json"):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("query_ids") == [query_id] and list(rec.get("doc_ids") or []) == list(doc_ids):
            out.append((path.stat().st_mtime, path, rec))
    return sorted(out, reverse=True)


def repair_quotes(data, encoding, docs, corpus):
    """Apply R1 to every quote in the payload. Returns (new_data, changes, unrepairable)."""
    changes, unrepairable = [], []
    if encoding == "compact":
        for i, proof in enumerate(data.get("proofs") or []):
            di = proof.get("document")
            if not isinstance(di, int) or not 0 <= di < len(docs):
                continue
            src = docs[di]["text"]
            quote = proof.get("quote")
            if not isinstance(quote, str) or verifies(quote, src):
                continue
            fixed, why = restore_quote(quote, src)
            if fixed and verifies(fixed, src):
                proof["quote"] = fixed
                changes.append({"where": f"proofs[{i}]", "doc_id": docs[di]["doc_id"],
                                "rule": "R1_glyph_restore", "rules_version": RULES_VERSION,
                                "before": quote, "after": fixed})
            else:
                unrepairable.append({"where": f"proofs[{i}]", "doc_id": docs[di]["doc_id"],
                                     "detail": why, "level": match_level(quote, src)[0]})
    else:
        for qid, bydoc in (data.get("evidence") or {}).items():
            if not isinstance(bydoc, dict):
                continue
            for doc_id, item in bydoc.items():
                if not isinstance(item, dict):
                    continue
                src = (corpus.get(doc_id) or {}).get("text", "")
                quote = item.get("quote")
                if not isinstance(quote, str) or verifies(quote, src):
                    continue
                fixed, why = restore_quote(quote, src)
                if fixed and verifies(fixed, src):
                    item["quote"] = fixed
                    changes.append({"where": f"evidence[{qid}][{doc_id}]", "doc_id": doc_id,
                                    "rule": "R1_glyph_restore", "rules_version": RULES_VERSION,
                                    "before": quote, "after": fixed})
                else:
                    unrepairable.append({"where": f"evidence[{qid}][{doc_id}]", "doc_id": doc_id,
                                         "detail": why, "level": match_level(quote, src)[0]})
    return data, changes, unrepairable


def classify_failure(exc) -> str:
    msg = str(exc)
    for key, bucket in STRUCTURAL.items():
        if key in msg:
            return bucket
    if "Expecting" in msg or "Unterminated" in msg or "delimiter" in msg:
        return "json_not_decodable"
    return "other:" + msg[:60]


def main() -> int:
    apply_mode = "--apply" in sys.argv
    corpus, queries, index = load_inputs()
    idx_by_label = {f"{i['query_id']}_batch_{i['batch']:02d}": i for i in index}

    failed = []
    for item in index:
        qid, n = item["query_id"], item["batch"]
        if (OUT_DIR / qid / f"batch_{n:02d}.json").exists():
            continue
        cands = responses_for(qid, item["docs"])
        if not cands:
            failed.append((f"{qid}_batch_{n:02d}", item, [], "never_attempted"))
        else:
            failed.append((f"{qid}_batch_{n:02d}", item, cands, "attempted"))

    never = [f for f in failed if f[3] == "never_attempted"]
    attempted = [f for f in failed if f[3] == "attempted"]
    print(f"待处理 {len(failed)} 批 = 从未尝试 {len(never)} 批 + 已尝试未通过 {len(attempted)} 批")
    print(f"  从未尝试合计 {sum(i['blocks'] for _, i, _, _ in never)} 条")

    repaired, todo = [], []
    for label, item, cands, _ in attempted:
        qid = item["query_id"]
        docs = [corpus[d] for d in item["docs"]]
        qlist = [queries[qid]]
        outcome = None
        first_err = None
        for _, path, rec in cands:                      # newest response first
            encoding = rec.get("encoding") or "compact"
            raw = rec.get("response")
            if not isinstance(raw, str) or not raw.strip():
                continue
            try:
                data = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            except Exception as exc:                    # noqa: BLE001
                first_err = first_err or f"{encoding}:{classify_failure(exc)}"
                continue
            original = json.dumps(data, ensure_ascii=False)
            data, changes, unrepairable = repair_quotes(data, encoding, docs, corpus)
            fixed_raw = json.dumps(data, ensure_ascii=False)
            for trial, note in ((original, "as_saved"), (fixed_raw, "after_R1")):
                try:
                    (annotate.parse_compact if encoding == "compact" else annotate.parse_response)(
                        trial, qlist, docs)
                except Exception as exc:                # noqa: BLE001
                    first_err = first_err or f"{encoding}:{classify_failure(exc)}"
                    continue
                outcome = (encoding, trial, note, changes, unrepairable, path.stem)
                break
            if outcome and (outcome[2] == "after_R1" or not unrepairable):
                break
        if outcome and outcome[1]:
            encoding, good_raw, note, changes, unrepairable, rid = outcome
            repaired.append({"label": label, "item": item, "encoding": encoding,
                             "raw": good_raw, "note": note, "changes": changes,
                             "unrepairable": unrepairable, "response_id": rid})
        else:
            todo.append({"label": label, "query_id": qid, "blocks": item["blocks"],
                         "reason": first_err or "no_usable_response",
                         "response_ids": [p.stem[:16] for _, p, _ in cands][:4]})

    print(f"\n本地机械修复(R1 字形引文)后可通过校验: {len(repaired)} 批")
    for r in repaired:
        print(f"  {r['label']}  [{r['encoding']}] {r['note']}  改引文 {len(r['changes'])} 处"
              + (f"｜仍有未修复引文 {len(r['unrepairable'])} 处" if r["unrepairable"] else ""))
    print(f"需定点重判: {len(todo)} 批 / {sum(t['blocks'] for t in todo)} 条")
    for t in todo:
        print(f"  {t['label']}  {t['reason']}")

    if not apply_mode:
        print("\n--dry-run：未写入任何文件")
        return 0

    log = {"rules_version": RULES_VERSION, "mode": "apply",
           "repaired": len(repaired), "rejudge": len(todo), "changes": []}
    for r in repaired:
        item = r["item"]
        qid, n = item["query_id"], item["batch"]
        rows = (annotate.parse_compact if r["encoding"] == "compact" else annotate.parse_response)(
            r["raw"], [queries[qid]], [corpus[d] for d in item["docs"]])
        payload = {"query_id": qid, "batch": n, "judgments": rows, "usage": {},
                   "encoding": r["encoding"], "source": "api_local_repair_R1"}
        (OUT_DIR / qid / f"batch_{n:02d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        log["changes"].append({"label": r["label"], "encoding": r["encoding"], "note": r["note"],
                               "response_id": r["response_id"], "quote_changes": r["changes"],
                               "unrepaired_quotes_left": r["unrepairable"]})
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    TODO_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已修复写入 {len(repaired)} 批 -> 留痕 {LOG_PATH.name}")
    print(f"定点重判清单 {len(todo)} 批 -> {TODO_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
