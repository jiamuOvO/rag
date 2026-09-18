"""Build targeted re-judgement task files for records that failed mechanical repair.

Only the affected blocks are included -- not the whole question -- so a re-judgement costs a
fraction of a full question and cannot disturb the records that already passed.

Sources of affected blocks:
  * quote_unresolved        quote matches nothing in the corpus -> re-judge that block
  * duplicate_doc_id        the block was judged twice, possibly against another block's text
  * pool_doc_never_judged   a pool block got no judgement (usually the twin of a fabricated id)
  * id_absent_from_corpus   fabricated id -> the record is dropped, the real block is re-judged

Usage: python -B tests/biomass_furan/make_retask_files.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "retask"
LINE_CAP = 1800

DROP_ISSUES = {"id_absent_from_corpus", "doc_id_not_in_pool"}


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def wrap(text: str, cap: int = LINE_CAP) -> str:
    flat = " ".join(text.split())
    return "\n".join(flat[i:i + cap] for i in range(0, len(flat), cap)) if flat else "(空块)"


def main() -> None:
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    todo = json.loads((ROOT / "repair_todo.json").read_text(encoding="utf-8"))
    log = json.loads((ROOT / "repair_log.json").read_text(encoding="utf-8"))

    prior: dict[tuple[str, str], dict] = {}
    for path in sorted((ROOT / "judgments").glob("*.json")):
        for row in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
            prior.setdefault((path.stem, row["doc_id"]), row)

    per_query: dict[str, dict] = defaultdict(lambda: {"rejudge": [], "drop": []})
    for item in todo:
        qid = item["query_id"]
        if item["issue"] in DROP_ISSUES:
            per_query[qid]["drop"].append(item["doc_id"])
        elif item["issue"] in {"quote_unresolved", "duplicate_doc_id", "pool_doc_never_judged"}:
            if item["doc_id"] not in per_query[qid]["rejudge"]:
                per_query[qid]["rejudge"].append(item["doc_id"])

    OUT.mkdir(exist_ok=True)
    written = 0
    for qid, work in sorted(per_query.items()):
        q = queries[qid]
        lines = [
            f"题目编号: {qid}",
            f"问题: {q['question']}",
            f"类型: {q['question_type']} | 难度: {q['difficulty']} | 语言: {q['language']} | 有答案: {q['answerable']}",
            "",
            "必须覆盖的事实:",
        ]
        for fact in q.get("required_facts") or []:
            lines.append(f"  - {fact}")
        lines += [
            "",
            f"参考答案: {q.get('reference_answer') or '(无)'}",
            "",
            "=" * 64,
            f"需要重新判断的块: {len(work['rejudge'])} 个。请对每一个块依据其完整正文重新给出 0/1/2/3。",
            "原判定放在每个块下方仅供参考，**不要因为它而迁就**；与原判定不同时请说明原因。",
            "=" * 64,
            "",
        ]
        for doc_id in work["rejudge"]:
            doc = corpus.get(doc_id)
            if doc is None:
                lines += [f"### doc_id={doc_id}", "  (该 id 不在冻结语料中，跳过)", ""]
                continue
            lines.append(f"### doc_id={doc_id} | 来源={doc['source_id']} | 标题={doc['title']}")
            old = prior.get((qid, doc_id))
            if old:
                quotes = old.get("evidence_quotes") or []
                lines.append(f"【原判定】等级={old.get('relevance')}")
                if quotes:
                    lines.append(f"【原引文】{quotes[0][:200]}")
                lines.append(f"【原理由】{str(old.get('reason') or '')[:200]}")
            else:
                lines.append("【原判定】无（该块此前未被判断）")
            lines.append("【块正文】")
            lines.append(wrap(doc["text"]))
            lines.append("")
        if work["drop"]:
            lines += [
                "=" * 64,
                "以下 id 将被删除（无效或不在池中），无需判断:",
            ]
            for doc_id in work["drop"]:
                lines.append(f"  - {doc_id}")
            lines.append("")
        (OUT / f"{qid}.txt").write_text("\n".join(lines), encoding="utf-8")
        written += 1
        print(f"  {qid}: 重判 {len(work['rejudge'])} 块 | 删除 {len(work['drop'])} 条")

    print(f"\n生成 {written} 个定点重判文件 -> {OUT}")


if __name__ == "__main__":
    main()
