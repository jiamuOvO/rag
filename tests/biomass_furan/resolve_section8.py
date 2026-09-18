"""Decide the 27 section-8 candidates under the agreed standard: EXPLICIT attribution to the named study.

Section 8 caps another paper's INDEPENDENT statement of the same fact at grade 1 when the question
names a source. The exception is a block that attributes the fact to the named study. The accepted
evidence for attribution is narrow, and the following are explicitly NOT evidence:

  * generic domain vocabulary shared by every title in the corpus (the earlier mistake: a title-word
    match on "biomass"/"lignocellulosic"/"pretreatment" proved nothing)
  * the fact happening to be the same
  * the model's reason claiming the block is from the same paper

Accepted:
  * an author-year citation of the named study (first-author surname + year)
  * the named study's DISTINCTIVE title words (tokens rare across the corpus's titles)
  * a numbered citation ON the fact sentence, resolved through the block's OWN paper's reference
    list and confirmed to point at the named study. The reference list is used only to identify
    what a number points at; no answer content is imported from other blocks.

Attribution alone does not settle the grade: the block must still be checked for which required
facts it actually supports. Outcomes: attributed (grade from fact coverage), independent (max 1),
or undeterminable (left pending).

Usage: python -B tests/biomass_furan/resolve_section8.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "section8_resolution.json"
sys.path.insert(0, str(ROOT))
from verify_judgments import match_level  # noqa: E402

CITE = re.compile(r"\[(\d{1,3})(?:\s*[,\-–]\s*\d{1,3})*\]")
REF_ENTRY = re.compile(r"(?:^|\s)(\d{1,3})\.\s+([A-Z][^.]{5,400}?)(?=\s\d{1,3}\.\s+[A-Z]|$)")
YEAR = re.compile(r"(19|20)\d{2}")


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def fold(text: str) -> str:
    return " ".join(text.split())


def title_phrase_hits(title: str, text_lower: str) -> list[str]:
    """Contiguous runs of >=4 consecutive title words appearing in the block.

    A single rare-ish word is not a paper name: "products", "value", "added" pass any per-word
    document-frequency filter yet identify nothing. Only a multi-word phrase counts.
    """
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z\-']+", title or "")]
    hits = []
    for size in (6, 5, 4):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if phrase in text_lower:
                hits.append(phrase)
        if hits:
            break
    return hits


def main() -> int:
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    labels = {}
    for path in sorted((ROOT / "judgments").glob("q_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[(path.stem, row["doc_id"])] = row
    ledger = {(c["query_id"], c["doc_id"]): c for c in
              json.loads((ROOT / "semantic_corrections.json").read_text(encoding="utf-8"))}
    pending = json.loads((ROOT / "semantic_pending_review.json").read_text(encoding="utf-8"))
    targets = sorted({(p["query_id"], p["doc_id"]) for p in pending
                      if p.get("kind") == "section8_needs_adjudication" and p.get("doc_id")})

    # Per-paper identity, and a corpus-wide document frequency so a "distinctive" title word really
    # is distinctive rather than shared domain vocabulary.
    by_source = defaultdict(list)
    for c in corpus.values():
        by_source[c["source_id"]].append(c)
    title_tokens = defaultdict(set)
    for sid, chunks in by_source.items():
        title_tokens[sid] = {w.lower() for w in re.findall(r"[A-Za-z]{5,}", chunks[0].get("title") or "")}
    df = Counter()
    for sid, toks in title_tokens.items():
        for t in toks:
            df[t] += 1

    def identity(sid):
        chunks = by_source.get(sid) or []
        if not chunks:
            return None
        c = chunks[0]
        authors = c.get("authors") or ""
        surname = ""
        m = re.search(r"([A-Z][A-Za-z\-']{2,})", str(authors))
        if m:
            surname = m.group(1)
        return {"title": c.get("title") or "", "year": str(c.get("year") or ""),
                "surname": surname,
                "distinctive": {t for t in title_tokens[sid] if df[t] <= 2}}

    def reference_lists(sid):
        """{number: entry text} built from the paper's own chunks."""
        refs = {}
        for chunk in by_source.get(sid, []):
            text = fold(chunk.get("text", ""))
            if not re.search(r"\bReferences\b|\bREFERENCES\b|\[\d{1,3}\]", text):
                continue
            for num, body in REF_ENTRY.findall(text):
                refs.setdefault(int(num), body[:300])
        return refs

    results = {"attributed": [], "independent_max1": [], "undeterminable": []}
    for qid, doc_id in targets:
        q = queries[qid]
        doc = corpus.get(doc_id)
        row = labels.get((qid, doc_id), {})
        named = (q.get("source_group") or [None])[0]
        ident = identity(named)
        text = fold(doc.get("text", "")) if doc else ""
        lower = text.lower()
        evidence = []
        if ident is None:
            results["undeterminable"].append({"query_id": qid, "doc_id": doc_id,
                                              "why": "指定文献在语料中没有对应来源，无法确定其身份"})
            continue
        if ident["surname"] and re.search(rf"\b{re.escape(ident['surname'])}\b[^.]{{0,40}}{ident['year']}",
                                          text) and ident["year"]:
            evidence.append(f"作者—年份引用：{ident['surname']} … {ident['year']}")
        hit = title_phrase_hits(ident["title"], lower)
        if hit:
            evidence.append(f"指定文献标题短语命中：{hit[0][:80]}")
        refs = reference_lists(doc["source_id"]) if doc else {}
        for m in CITE.finditer(text):
            for num in re.findall(r"\d{1,3}", m.group(0)):
                entry = refs.get(int(num))
                if not entry:
                    continue
                if (ident["surname"] and ident["surname"].lower() in entry.lower()) or \
                   any(w in entry.lower() for w in ident["distinctive"]):
                    evidence.append(f"编号引用 [{num}] 经该文参考文献表确认指向指定文献：{entry[:90]}")

        facts = q.get("fact_evidence") or []
        supported = sum(1 for f in facts if match_level(f.get("quote", ""), text)[0] <= 2)
        rec = {"query_id": qid, "doc_id": doc_id, "grade_now": row.get("relevance"),
               "named_source": named, "block_source": doc["source_id"] if doc else None,
               "named_title": ident["title"][:80],
               "facts_total": len(facts), "facts_supported": supported,
               "attribution_evidence": evidence,
               "reason": str(row.get("reason"))[:180]}
        if not evidence:
            rec["outcome"] = "independent_max1"
            rec["basis"] = "未发现对指定文献的显式归因（作者—年份 / 独有标题词 / 可解析的编号引用均无）"
            results["independent_max1"].append(rec)
        else:
            rec["outcome"] = "attributed"
            rec["basis"] = "发现显式归因；等级仍需按该块实际支持的必需事实判定（不自动等于 2/3）"
            results["attributed"].append(rec)

    OUT.write_text(json.dumps({
        "standard": "显式归因且能定位到指定文献；通用关键词/事实相同/模型声称同文献均不算归因证据",
        "targets": len(targets),
        "counts": {k: len(v) for k, v in results.items()},
        "detail": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"§八 待裁决 {len(targets)} 条 → 按新标准复判")
    for k, v in results.items():
        print(f"  {k}: {len(v)}")
    for k in ("attributed", "independent_max1"):
        for r in results[k][:6]:
            ev = "；".join(r.get("attribution_evidence") or []) or "无"
            print(f"  [{k}] {r['query_id']} {r['doc_id'][:16]} 现{r['grade_now']} "
                  f"事实 {r['facts_supported']}/{r['facts_total']}｜{ev[:110]}")
    for r in results["undeterminable"][:6]:
        print(f"  [undeterminable] {r['query_id']} {r['doc_id'][:16]} — {r['why'][:80]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
