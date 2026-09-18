"""Apply the semantic corrections the spot-check and screen established, with a separate ledger.

Two kinds of entry, kept strictly apart:

  applied   a correction whose rule and inputs are both objective:
            ANNOTATION_INSTRUCTIONS section 8 -- when a question names a source, another paper's
            independent statement of the same fact is grade 1, never 2/3. The predicate uses the
            structured fields (queries.source_group vs corpus.source_id) and only fires when the
            reason does NOT claim the block attributes the fact to the named source. Every applied
            entry cites the rule and carries before/after.
  pending   a located risk that is NOT rule-determined and must be re-read before any change:
            P1 (reason invokes an L1 rule but the grade is 2/3) and reasons that contradict their
            own grade. A reason string is a statement about a judgement, not evidence for a new one.

The ledger is written to semantic_corrections.json and every corrected pair is listed there so
merge_gapfill.py will not overwrite it on a later re-merge.

Usage:
    python -B tests/biomass_furan/apply_semantic_corrections.py --dry-run
    python -B tests/biomass_furan/apply_semantic_corrections.py --apply
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JUDGES = ROOT / "judgments"
QRELS = ROOT / "qrels.tsv"
LEDGER = ROOT / "semantic_corrections.json"
APPLIED_LOG = ROOT / "semantic_corrections_applied.json"
RULE = "ANNOTATION_INSTRUCTIONS 第八节：题目点名具体文献时，另一篇论文独立陈述相同事实只能判 1"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def genuine_attribution(reason: str) -> bool:
    """True only when the reason says the block attributes the fact TO the named source.

    Deliberately narrow: 'not attributed to the named review' must not read as an attribution.
    """
    text = reason.lower()
    for marker in ("attributed to", "归因于", "引述", "according to", "quoted from", "as cited in"):
        idx = 0
        while True:
            idx = text.find(marker, idx)
            if idx < 0:
                break
            window = text[max(0, idx - 22):idx]
            if not any(neg in window for neg in ("not ", "no ", "未", "不是", "非")):
                return True
            idx += len(marker)
    return False


def main() -> int:
    apply_mode = "--apply" in sys.argv
    screen = json.loads((ROOT / "semantic_risk_screen.json").read_text(encoding="utf-8"))
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    labels = {}
    for path in sorted(JUDGES.glob("q_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            labels[(path.stem, row["doc_id"])] = row

    applied, pending = [], []
    for e in screen["hits"]["P2_named_source_other_paper"]:
        key = (e["query_id"], e["doc_id"])
        row = labels.get(key)
        named = queries.get(e["query_id"], {}).get("source_group") or []
        if not row or row.get("relevance") not in (2, 3) or not named:
            continue
        doc_source = corpus.get(e["doc_id"], {}).get("source_id")
        if doc_source in named:
            continue
        if genuine_attribution(str(row.get("reason") or "")):
            pending.append({**e, "kind": "attribution_exception",
                            "note": "理由声明该块归因于指定文献，§八 例外，需回读确认"})
            continue
        applied.append({"query_id": e["query_id"], "doc_id": e["doc_id"],
                        "kind": "section8_named_source_cap",
                        "rule": RULE,
                        "named_source": named, "block_source": doc_source,
                        "before": row.get("relevance"), "after": 1,
                        "before_reason": str(row.get("reason"))[:240]})

    # Corrections made after ACTUALLY READING the block during the semantic spot-check. Each entry
    # pins its target by (question, current grade, a distinctive fragment of the reason that was
    # read) so it can only fire on the record it was read against. Nothing here is inferred from
    # reason text alone -- the reading is the basis, the fragment is only the pointer.
    read_based = [
        ("q_0004", 2, 1, "xylose loading not SnCl4 dosage",
         "块写的是木糖载量(10->20 wt%)变化，题目问的是 SnCl4 用量(5->10 mol%)变化。变量不同，"
         "按规范属 L1a（同体系不同条件）= 1，不能判 2"),
        ("q_0007", 2, 1, "does not state the optimized values",
         "理由自称 L1b（同一论文的优化方法/数据基础）且明说未给出优化值。L1b 按定义是 1 级，"
         "等级与理由矛盾"),
        ("q_0008", 2, 1, "recycling yields, not the predicted",
         "块给的是循环使用收率，题目要求区分预测值与实测值。不同条件属 L1a = 1"),
        ("q_0042", 3, 2, "catalyst",
         "题目有 2 项必需事实（一项来自磷酸法论文、一项来自 DES 论文）。已回读该题全部三级块："
         "内容均为 ChCl/GA DES 体系，只支撑第 2 项，必需事实原文命中 1/2。三级要求单独覆盖"
         "全部必需事实，只能判 2", 5),
    ]
    for entry in read_based:
        qid, expect, target, fragment, basis = entry[:5]
        want = entry[5] if len(entry) > 5 else 1
        found = [(q, d) for (q, d), r in labels.items()
                 if q == qid and r.get("relevance") == expect and fragment.lower() in str(r.get("reason", "")).lower()]
        if len(found) != want:
            pending.append({"query_id": qid, "doc_id": None, "kind": "read_based_anchor_not_unique",
                            "note": f"按「{fragment}」定位到 {len(found)} 条（期望 {want} 条），需人工指定"})
            continue
        for (q, d) in found:
            applied.append({"query_id": q, "doc_id": d,
                            "kind": "read_based_spotcheck_correction", "rule": basis,
                            "before": expect, "after": target,
                            "before_reason": str(labels[(q, d)].get("reason"))[:240]})

    for e in screen["hits"]["P4_grade3_without_full_fact_coverage"]:
        # LOCATOR ONLY. Fact presence is tested against the fact's own frozen quote, but a block can
        # state a required fact in different words, so a low hit count is a signal to re-read, not a
        # verdict. Every one of these goes to the pending list.
        pending.append({**e, "kind": "P4_grade3_partial_fact_quote_hits",
                        "note": f"必需事实原文命中 {e['facts_supported']}/{e['facts_total']}。"
                                f"三级要求覆盖全部必需事实，但事实可能换措辞表达——需回读原块后再定；不自动改"})

    for e in screen["hits"]["P1_L1_rule_with_high_grade"]:
        if any(a["query_id"] == e["query_id"] and a["doc_id"] == e["doc_id"] for a in applied):
            continue
        pending.append({**e, "kind": "P1_reason_cites_L1_but_grade_high",
                        "note": "1 级规则与 2/3 级并存，需回读原块判断是「理由误写」还是「等级误判」；不自动改"})

    print(f"扫描命中 P1 {len(screen['hits']['P1_L1_rule_with_high_grade'])}｜"
          f"P2 {len(screen['hits']['P2_named_source_other_paper'])}｜"
          f"P3 {len(screen['hits']['P3_claims_full_coverage_at_2'])}｜"
          f"P4 {len(screen['hits']['P4_grade3_without_full_fact_coverage'])}")
    print(f"可确定的修正: {len(applied)} 条"
          f"（§八 封顶 {sum(1 for a in applied if a['kind']=='section8_named_source_cap')}｜"
          f"回读后修正 {sum(1 for a in applied if a['kind']=='read_based_spotcheck_correction')}）")
    print(f"仅定位、需回读后定论（待定）: {len(pending)} 条")
    for a in applied[:6]:
        print(f"  {a['query_id']} {a['doc_id'][:18]} {a['before']} -> {a['after']}"
              f"（{a['kind']}）")
    print()
    if not apply_mode:
        print("--dry-run：未写入任何文件")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup = ROOT / f"judgments_backup_semantic_{stamp}"
    shutil.copytree(JUDGES, backup)
    shutil.copy2(QRELS, backup / "qrels.tsv")
    print(f"已备份 -> {backup.name}")

    for a in applied:
        labels[(a["query_id"], a["doc_id"])]["relevance"] = a["after"]
        labels[(a["query_id"], a["doc_id"])]["review_status"] = "AI_spotcheck_corrected"
        labels[(a["query_id"], a["doc_id"])]["correction_kind"] = a["kind"]
    for qid in {a["query_id"] for a in applied}:
        rows = sorted((r for (q, _), r in labels.items() if q == qid), key=lambda r: r["doc_id"])
        (JUDGES / f"{qid}.json").write_text(
            json.dumps({"query_id": qid, "judgments": rows}, ensure_ascii=False, indent=1),
            encoding="utf-8")

    all_rows = sorted((q, d, r.get("relevance")) for (q, d), r in labels.items())
    with QRELS.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("query_id\tdoc_id\trelevance\n")
        for q, d, rel in all_rows:
            fh.write(f"{q}\t{d}\t{rel}\n")

    # The ledger ACCUMULATES. A later run re-derives only what is still un-corrected, so writing it
    # fresh would erase the record of earlier corrections.
    merged_ledger = {c["query_id"] + "\t" + c["doc_id"]: c for c in applied}
    if LEDGER.exists():
        for c in json.loads(LEDGER.read_text(encoding="utf-8")):
            merged_ledger.setdefault(c["query_id"] + "\t" + c["doc_id"], c)
    merged_ledger = [merged_ledger[k] for k in sorted(merged_ledger)]
    LEDGER.write_text(json.dumps(merged_ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    (ROOT / "semantic_pending_review.json").write_text(
        json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    APPLIED_LOG.write_text(json.dumps({
        "backup": backup.name, "applied": len(applied), "pending": len(pending),
        "rule": RULE, "qrels_rows": len(all_rows),
        "note": "这些配对被 merge_gapfill.py 视为已修正，后续重新合并不会覆盖。",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已修正 {len(applied)} 条 -> {LEDGER.name}")
    print(f"待定 {len(pending)} 条 -> semantic_pending_review.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
