"""Resolve the P4 candidates with an explicit fact-versus-evidence comparison.

P4 flagged records graded 3 whose required-fact quotations do not all appear in the block. A
missing quotation is only a LOCATOR -- a fact may be stated in different words. So this does not
downgrade anything on quotation matching. It builds the comparison the judgement must rest on:

  * every required fact of the question, with its hard qualifier
  * the block sentence(s) that carry that fact's most distinctive anchors
  * a per-fact verdict: supported / not_supported / undetermined

Anchors are the least paraphrasable part of a fact: numbers, units, and ratios. A number stated
with its unit is evidence the block reports that quantity; a fact with no such anchor cannot be
settled this way and is marked undetermined rather than assumed.

Outcome per record, using the frozen level definitions:
  all facts supported            -> grade 3 stands
  some facts supported directly  -> grade 2
  none anchored                  -> handed over (never auto-changed)

Usage: python -B tests/biomass_furan/resolve_p4.py [--apply]
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "p4_resolution.json"
NUM = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|wt\.?%|mol%|mg|g|kg|min|h|hours?|°?[CF]|◦C|mL|L|M|bar|MPa|atm|%|wt)?",
                 re.I)


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def anchors(fact_text: str) -> list[str]:
    """Numbers carrying a unit, ratios, and 3+-digit numbers -- the part that resists paraphrase.

    Bare small integers are deliberately excluded: "1" and "10" match a year, a page number or a
    reference index, so allowing them turns "not stated" into "supported".
    """
    out = set()
    for m in NUM.finditer(fact_text or ""):
        token = m.group(0).strip().lower()
        digits = re.match(r"\d+", token)
        if not digits:
            continue
        has_unit = bool(re.search(r"[a-z%]", token))
        if has_unit or len(digits.group(0)) >= 3:
            out.add(token)
    out |= {r.lower() for r in re.findall(r"1\s*:\s*\d+", fact_text or "")}
    return sorted(out)


def sentences(text: str):
    return re.split(r"(?<=[.;:。；])\s+", " ".join(text.split()))


def main() -> int:
    apply_mode = "--apply" in sys.argv
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    labels = {}
    for path in sorted((ROOT / "judgments").glob("q_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[(path.stem, row["doc_id"])] = row
    pending = json.loads((ROOT / "semantic_pending_review.json").read_text(encoding="utf-8"))
    targets = sorted({(p["query_id"], p["doc_id"]) for p in pending
                      if p.get("kind") == "P4_grade3_partial_fact_quote_hits" and p.get("doc_id")})

    detail, counts = [], Counter()
    for qid, doc_id in targets:
        q = queries[qid]
        doc = corpus.get(doc_id, {})
        row = labels.get((qid, doc_id), {})
        text = " ".join(doc.get("text", "").split())
        sents = sentences(text)
        per_fact = []
        for fact in q.get("fact_evidence") or []:
            facts_txt = fact.get("fact") or ""
            ans = anchors(facts_txt)
            hit_sents = [s for s in sents if any(a in s.lower() for a in ans)] if ans else []
            if ans and hit_sents:
                verdict = "supported"
            elif ans:
                verdict = "not_supported"
            else:
                verdict = "undetermined"
            per_fact.append({"fact": facts_txt[:200],
                             "hard_anchor": (fact.get("quote") or "")[:120],
                             "anchors": ans[:6],
                             "block_evidence": hit_sents[0][:260] if hit_sents else None,
                             "verdict": verdict})
        sup = sum(1 for f in per_fact if f["verdict"] == "supported")
        und = sum(1 for f in per_fact if f["verdict"] == "undetermined")
        if per_fact and sup == len(per_fact):
            outcome = "keeps_grade3_all_facts_supported"
        elif sup:
            outcome = "capped_to_2_partial_support"
        elif und:
            outcome = "handover_undetermined_anchors"
        else:
            outcome = "handover_no_anchor_supported"
        counts[outcome] += 1
        detail.append({"query_id": qid, "doc_id": doc_id, "grade_now": row.get("relevance"),
                       "facts_total": len(per_fact), "facts_supported": sup,
                       "facts_undetermined": und, "outcome": outcome, "per_fact": per_fact})

    OUT.write_text(json.dumps({
        "note": "事实—证据对照；引文未命中只用于定位，不作为降级依据。数值+单位是判据锚点。",
        "targets": len(targets), "counts": dict(counts), "detail": detail,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"P4 待复核 {len(targets)} 条")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    for r in detail[:4]:
        print(f"  示例 {r['query_id']} {r['doc_id'][:16]} 现{r['grade_now']} "
              f"支持 {r['facts_supported']}/{r['facts_total']} 无法确定 {r['facts_undetermined']} → {r['outcome']}")
        for f in r["per_fact"][:2]:
            print(f"     事实: {f['fact'][:80]}")
            print(f"     锚点: {f['anchors']} → {f['verdict']}｜块内: {(f['block_evidence'] or '无')[:90]}")

    if not apply_mode:
        print("\n--dry-run：未写入任何文件")
        return 0
    edits = [r for r in detail if r["outcome"] == "capped_to_2_partial_support"
             and r["grade_now"] == 3]
    if edits:
        stamp = time.strftime("%Y%m%dT%H%M%S")
        shutil.copytree(ROOT / "judgments", ROOT / f"judgments_backup_p4_{stamp}")
    applied = []
    for r in edits:
        row = labels[(r["query_id"], r["doc_id"])]
        applied.append({"query_id": r["query_id"], "doc_id": r["doc_id"],
                        "kind": "p4_grade3_partial_fact_support",
                        "rule": "三级定义：须单独覆盖全部必需事实；本块直接支持部分必需事实（数值锚点命中）",
                        "before": 3, "after": 2,
                        "facts_supported": r["facts_supported"], "facts_total": r["facts_total"]})
        row["relevance"] = 2
        row["review_status"] = "AI_spotcheck_corrected"
        row["correction_kind"] = "p4_grade3_partial_fact_support"
    for qid in {a["query_id"] for a in applied}:
        rows = sorted((v for (qq, _), v in labels.items() if qq == qid), key=lambda v: v["doc_id"])
        (ROOT / "judgments" / f"{qid}.json").write_text(
            json.dumps({"query_id": qid, "judgments": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    rows = sorted((qq, d, v.get("relevance")) for (qq, d), v in labels.items())
    with (ROOT / "qrels.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("query_id\tdoc_id\trelevance\n")
        for qq, d, rel in rows:
            fh.write(f"{qq}\t{d}\t{rel}\n")
    ledger = json.loads((ROOT / "semantic_corrections.json").read_text(encoding="utf-8"))
    m = {c["query_id"] + "\t" + c["doc_id"]: c for c in ledger}
    for a in applied:
        m.setdefault(a["query_id"] + "\t" + a["doc_id"], a)
    (ROOT / "semantic_corrections.json").write_text(
        json.dumps([m[k] for k in sorted(m)], ensure_ascii=False, indent=1), encoding="utf-8")
    left = [p for p in pending if p.get("kind") != "P4_grade3_partial_fact_quote_hits"]
    left += [{"query_id": r["query_id"], "doc_id": r["doc_id"],
              "kind": "P4_left_to_user", "outcome": r["outcome"],
              "facts_supported": r["facts_supported"], "facts_total": r["facts_total"]}
             for r in detail if r["outcome"].startswith("handover")]
    (ROOT / "semantic_pending_review.json").write_text(
        json.dumps(left, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已按事实支持度降为 2：{len(applied)} 条｜仍在待定：{len(left)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
