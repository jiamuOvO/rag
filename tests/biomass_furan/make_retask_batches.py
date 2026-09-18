"""Build targeted re-judgement batches for pairs whose compact AND perblock calls both failed.

Re-sending an identical prompt to a batch that already failed both encodings just reproduces the
defect (temperature 0), so this changes the request in the two ways the evidence supports:

  1. ONLY the affected blocks are included. A block that already carries a validated judgement is
     not asked again -- that shrinks the batch, which is exactly what the failure modes depend on
     (a shorter grade string, fewer evidence keys to keep aligned).
  2. The specific defect of the previous attempt is stated in the prompt
     (`prior_attempt_note`), naming only WHAT was wrong, never an answer or a grade.

Task files take a FREE batch number for their question, so no existing batch is renumbered or
overwritten, and the entries are registered in the supplement index the runner already reads.

Usage:
    python -B tests/biomass_furan/make_retask_batches.py --dry-run
    python -B tests/biomass_furan/make_retask_batches.py --apply
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gapfill"
SUPPLEMENT = OUT / "_gapfill_supplement.json"
LINE_CAP = 1800

sys.path.insert(0, str(ROOT))
import annotate  # noqa: E402


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def wrap(text: str, cap: int = LINE_CAP) -> str:
    flat = " ".join(text.split())
    return "\n".join(flat[i:i + cap] for i in range(0, len(flat), cap)) if flat else "(空块)"


def main() -> int:
    apply_mode = "--apply" in sys.argv
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    index = json.loads((OUT / "_gapfill_index.json").read_text(encoding="utf-8"))
    supplement = json.loads(SUPPLEMENT.read_text(encoding="utf-8")) if SUPPLEMENT.exists() else []
    rejudge = set(json.loads((ROOT / "run_full_rejudge_list.json").read_text(encoding="utf-8")))

    labelled = set()
    for line in (ROOT / "qrels.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            qid, doc_id, _ = line.split("\t")
            labelled.add((qid, doc_id))

    # newest response per (question, block set), to state the actual defect
    responses = defaultdict(list)
    for path in (ROOT / "runtime" / "model_responses").glob("*.json"):
        rec = json.loads(path.read_text(encoding="utf-8"))
        responses[(rec["query_ids"][0], tuple(rec["doc_ids"]))].append((path.stat().st_mtime, rec))
    for value in responses.values():
        value.sort(reverse=True)

    used_batches = defaultdict(set)
    for item in index + supplement:
        used_batches[item["query_id"]].add(item["batch"])

    entries, lines_report = [], []
    for item in index:
        label = f"{item['query_id']}_batch_{item['batch']:02d}"
        if label not in rejudge:
            continue
        qid = item["query_id"]
        affected = [d for d in item["docs"] if (qid, d) not in labelled]
        if not affected:
            lines_report.append((label, 0, "全部块已有标签 —— 无需重判，跳过"))
            continue

        defect = "未知（未找到可用原始响应）"
        for _, rec in responses.get((qid, tuple(item["docs"])), []):
            raw = rec.get("response")
            if not isinstance(raw, str) or not raw.strip():
                continue
            encoding = rec.get("encoding") or "compact"
            try:
                (annotate.parse_compact if encoding == "compact" else annotate.parse_response)(
                    raw, [queries[qid]], [corpus[d] for d in item["docs"]])
            except Exception as exc:                          # noqa: BLE001
                defect = f"{encoding}: {exc}"
                break

        note = ("上一次对同一组块的两道判定都未通过校验。缺陷是：" + defect +
                "。请对每个块单独给出等级，等级位数必须与块数一致，"
                "非零等级的引文必须是该块内逐字连续原文（可用省略号分段），"
                "每个非零等级都要有对应证据条目。")
        n = max(used_batches[qid] | {0}) + 1
        used_batches[qid].add(n)

        lines = [f"题目编号: {qid}", f"问题: {queries[qid]['question']}",
                 f"类型: {queries[qid]['question_type']} | 难度: {queries[qid]['difficulty']}",
                 "", "【定点重判】本批只含受影响块（已有合格判定或已单独处理的块不在其中）。",
                 f"【上次失败原因】{defect}", "", "必须覆盖的事实:"]
        for fact in queries[qid].get("required_facts") or []:
            lines.append(f"  - {fact}")
        lines += ["", f"参考答案: {queries[qid].get('reference_answer') or '(无)'}"]
        if queries[qid].get("source_group"):
            lines += ["", f"题目指定来源: {queries[qid]['source_group']}"]
        lines += ["", "=" * 64, f"本批 {len(affected)} 个块。逐个给出 0/1/2/3。", "=" * 64, ""]
        for j, doc_id in enumerate(affected, start=1):
            doc = corpus[doc_id]
            lines.append(f"[{j}] doc_id={doc_id} | 待标 | 来源={doc['source_id']} | 标题={doc['title']}")
            lines.append(wrap(doc["text"]))
            lines.append("")

        entries.append({"query_id": qid, "batch": n, "blocks": len(affected),
                        "recheck_pos": 0, "recheck_l1": 0, "new": len(affected),
                        "docs": affected, "retask_reason": defect,
                        "supplement_reason": "compact 与 perblock 均已失败后的定点重判"})
        lines_report.append((label, len(affected), defect[:70]))
        if apply_mode:
            path = OUT / qid / f"batch_{n:02d}.txt"
            if path.exists():
                raise SystemExit(f"拒绝覆盖已存在的任务文件 {path}")
            path.write_text("\n".join(lines), encoding="utf-8")

    print(f"定点重判批次 {len(entries)} 个"
          f"（原 26 批，只保留受影响块 → 块数 {sum(e['blocks'] for e in entries)}）")
    for label, n, defect in lines_report:
        print(f"  {label:18s} 受影响块 {n:>2d}  {defect}")
    if not apply_mode:
        print("\n--dry-run：未写入任何文件")
        return 0

    SUPPLEMENT.write_text(json.dumps(supplement + entries, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    print(f"\n已追加到 {SUPPLEMENT.name}（原索引与既有批次未改动）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
