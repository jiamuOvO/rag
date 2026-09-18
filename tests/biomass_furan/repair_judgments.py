"""Repair mechanical defects in annotation outputs, emitting a full before/after log.

Only ONE repair is mechanical and self-verifying, so only that one is automated:

  R1  glyph-folded quotes -> rewritten to the block's exact text

      Safe because the restore is derived from the block itself and re-checked at L1/L2
      afterwards. Nothing is written unless the restored quote re-verifies.

Everything that needs a fresh semantic judgement is NOT touched here; it is collected into
repair_todo.json instead:

  * quote that matches no fragment anywhere (L4)          -> re-judge the record
  * quote found, but in a DIFFERENT block                 -> decide keep / downgrade / re-quote
  * doc_id absent from the pool                           -> fabricate-or-typo, needs a decision
  * pool doc_id never judged                              -> needs a fresh judgement
  * duplicate doc_id rows                                 -> decide which row survives
  * missing / illegal fields                              -> needs a fresh judgement

Usage:
    python -B tests/biomass_furan/repair_judgments.py --dry-run
    python -B tests/biomass_furan/repair_judgments.py --apply
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RULES_VERSION = "v2"
ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)\s*")

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


def fold(text: str) -> str:
    """1:1 character mapping, so offsets survive the fold."""
    return text.translate(GLYPH_FOLD)


def flat_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace but remember where every surviving character came from."""
    out: list[str] = []
    origin: list[int] = []
    pending_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            pending_space = True
            continue
        if pending_space and out:
            out.append(" ")
            origin.append(i)
        out.append(ch)
        origin.append(i)
        pending_space = False
    return "".join(out), origin


def locate(fragment: str, source: str) -> tuple[int, int] | None:
    """Find the fragment in source, tolerating glyph and whitespace differences."""
    folded_src = fold(source)
    needle = flat_with_map(fold(fragment))[0]
    if not needle:
        return None
    hay, origin = flat_with_map(folded_src)
    pos = hay.find(needle)
    if pos < 0:
        return None
    # origin maps folded/flattened positions back to raw source offsets
    return origin[pos], origin[pos + len(needle) - 1] + 1


def restore_quote(quote: str, source: str) -> tuple[str | None, str]:
    """Return (restored_quote, note). restored_quote is None when any fragment cannot be located."""
    fragments = [f for f in ELLIPSIS.split(quote or "") if f.strip()]
    if not fragments:
        return None, "empty quote"
    parts: list[str] = []
    for fragment in fragments:
        span = locate(fragment, source)
        if span is None:
            return None, f"fragment not locatable: {fragment.strip()[:60]!r}"
        parts.append(source[span[0]:span[1]])
    return " … ".join(parts), ""


def verifies(quote: str, source: str) -> bool:
    """L1 or L2 only -- the relaxed tiers that count as a pass."""
    for fragment in [f for f in ELLIPSIS.split(quote or "") if f.strip()]:
        if fragment not in source and " ".join(fragment.split()) not in " ".join(source.split()):
            return False
    return True


def main() -> int:
    applied = "--apply" in sys.argv
    corpus = {c["doc_id"]: c["text"] for c in read_jsonl(ROOT / "corpus.jsonl")}
    pool = {p["query_id"]: [e["doc_id"] for e in p["pool"]] for p in read_jsonl(ROOT / "pool.jsonl")}

    log: list[dict] = []
    todo: list[dict] = []
    repaired = 0

    for path in sorted((ROOT / "judgments").glob("*.json")):
        qid = path.stem
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("judgments") or []
        pool_ids = pool.get(qid, [])
        seen = Counter(r.get("doc_id") for r in rows)
        changed = False

        for row in rows:
            did, rel = row.get("doc_id"), row.get("relevance")
            if did not in corpus:
                todo.append({"query_id": qid, "doc_id": did, "issue": "id_absent_from_corpus"})
                continue
            source = corpus[did]
            if rel in (2, 3):
                for idx, quote in enumerate(row.get("evidence_quotes") or []):
                    if verifies(quote, source):
                        continue
                    restored, note = restore_quote(quote, source)
                    if restored and verifies(restored, source):
                        log.append({"query_id": qid, "doc_id": did, "level": rel,
                                    "rule": "R1_glyph_restore", "rules_version": RULES_VERSION,
                                    "before": quote, "after": restored})
                        row["evidence_quotes"][idx] = restored
                        changed = True
                        repaired += 1
                    else:
                        hits = [k for k, t in corpus.items() if " ".join(restored.split())[:50] in " ".join(t.split())] if restored else []
                        todo.append({"query_id": qid, "doc_id": did, "level": rel, "issue": "quote_unresolved",
                                     "quote": quote[:200], "note": note,
                                     "found_in_other_blocks": [h for h in hits if h != did][:3]})
            elif rel in (0, 1):
                if not str(row.get("reason") or "").strip():
                    todo.append({"query_id": qid, "doc_id": did, "issue": "missing_reason"})

        for did, n in seen.items():
            if n > 1:
                todo.append({"query_id": qid, "doc_id": did, "issue": "duplicate_doc_id", "rows": n})
            if did not in pool_ids:
                todo.append({"query_id": qid, "doc_id": did, "issue": "doc_id_not_in_pool"})
        for did in pool_ids:
            if did not in seen:
                todo.append({"query_id": qid, "doc_id": did, "issue": "pool_doc_never_judged"})

        if changed and applied:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    (ROOT / "repair_log.json").write_text(json.dumps(
        {"rules_version": RULES_VERSION, "mode": "apply" if applied else "dry-run",
         "auto_repaired": repaired, "changes": log}, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "repair_todo.json").write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")

    kinds = Counter(t["issue"] for t in todo)
    print(f"模式: {'APPLY' if applied else 'DRY-RUN'}  |  规则版本 {RULES_VERSION}")
    print(f"自动修复(R1 字形引文还原): {repaired} 条  -> repair_log.json")
    print(f"待人工判断: {len(todo)} 项  -> repair_todo.json")
    for kind, n in kinds.most_common():
        print(f"    {kind}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
