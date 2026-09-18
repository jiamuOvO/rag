"""Create the supplementary gap-fill task for a scoped pair that no batch ever covered.

make_gapfill_tasks.py built batch_01..07 for q_0060, but chk_c189cd855d6d974fa4f8ee42 is inside
the frozen first-release scope and in NONE of those batches. Rather than renumber or rebuild the
existing batches (which would break every output already on disk), this writes ONE extra task in a
free slot and registers it in a separate supplement index. Nothing is overwritten or renumbered.

Usage:
    python -B tests/biomass_furan/make_supplement_task.py --dry-run
    python -B tests/biomass_furan/make_supplement_task.py --apply
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gapfill"
SUPPLEMENT = OUT / "_gapfill_supplement.json"
INDEX = OUT / "_gapfill_index.json"
LINE_CAP = 1800


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
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    index = json.loads(INDEX.read_text(encoding="utf-8"))
    frozen = [tuple(l.split("\t")) for l in
              json.loads((ROOT / "frozen_scope_1312.json").read_text(encoding="utf-8"))]
    covered = {(i["query_id"], d) for i in index for d in i["docs"]}

    orphans = sorted(p for p in frozen if p not in covered)
    labelled = set()
    for line in (ROOT / "qrels.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        if line.strip():
            qid, doc_id, _ = line.split("\t")
            labelled.add((qid, doc_id))
    need_task = [p for p in orphans if p not in labelled]
    print(f"首版清单内没有任何批次覆盖的组合: {len(orphans)} 条")
    print(f"  其中已有标签、无需补任务: {len(orphans) - len(need_task)} 条")
    print(f"  其中缺标签、需要补漏任务: {len(need_task)} 条")
    entries = []
    for qid, doc_id in need_task:
        used = {i["batch"] for i in index if i["query_id"] == qid}
        n = max(used) + 1 if used else 1
        doc = corpus[doc_id]
        q = queries[qid]
        lines = [f"题目编号: {qid}", f"问题: {q['question']}",
                 f"类型: {q['question_type']} | 难度: {q['difficulty']}",
                 "", "必须覆盖的事实:"]
        for fact in q.get("required_facts") or []:
            lines.append(f"  - {fact}")
        lines += ["", f"参考答案: {q.get('reference_answer') or '(无)'}"]
        if q.get("source_group"):
            lines += ["", f"题目指定来源: {q['source_group']}"]
        lines += ["", "=" * 64,
                  "本批 1 个块（补漏：该组合在首版候选清单内，但此前没有任何批次覆盖）。",
                  "逐个给出 0/1/2/3。", "=" * 64, ""]
        lines.append(f"[1] doc_id={doc_id} | 待标 | 来源={doc['source_id']} | 标题={doc['title']}")
        lines.append(wrap(doc["text"]))
        lines.append("")
        entries.append({"query_id": qid, "batch": n, "blocks": 1,
                        "recheck_pos": 0, "recheck_l1": 0, "new": 1, "docs": [doc_id],
                        "supplement_reason": "首版候选清单内、原批次未覆盖的漏项"})
        print(f"  {qid}_batch_{n:02d}（{qid} 已有批次 {sorted(used)} → 使用空闲编号 {n:02d}）"
              f" | {doc['title'][:50]}")
        if apply_mode:
            path = OUT / qid / f"batch_{n:02d}.txt"
            if path.exists():
                raise SystemExit(f"拒绝覆盖已存在的任务文件 {path}")
            path.write_text("\n".join(lines), encoding="utf-8")

    if not apply_mode:
        print("\n--dry-run：未写入任何文件")
        return 0
    SUPPLEMENT.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写出 {len(entries)} 条补漏任务 -> {SUPPLEMENT.name}")
    print(f"原 {INDEX.name} 与既有 batch 文件均未改动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
