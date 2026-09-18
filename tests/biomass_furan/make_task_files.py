"""Materialise per-question annotation task files: question + full text of every pool candidate.

One file per question in tasks/<query_id>.txt, so an annotator (or subagent) reads a single
file instead of a 10 MB corpus.  Long lines are hard-wrapped because the Read tool truncates
any single line over 2000 characters -- a truncated block would silently corrupt a judgment.

Usage: python -B tests/biomass_furan/make_task_files.py
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "tasks"
LINE_CAP = 1800


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def wrap(text: str, cap: int = LINE_CAP) -> str:
    """Collapse PDF line breaks, then hard-wrap.

    PDF extraction produces many short lines that are layout artefacts, not paragraphs.
    Flattening them keeps one question inside the Read tool's 2000-line page limit and the
    2000-character line truncation, so a block is never silently cut in half.
    """
    flat = " ".join(text.split())
    if not flat:
        return "(空块)"
    return "\n".join(flat[i:i + cap] for i in range(0, len(flat), cap))


def pages(metadata: object) -> str:
    if isinstance(metadata, str):
        try:
            metadata = ast.literal_eval(metadata)
        except (ValueError, SyntaxError):
            return "?"
    if isinstance(metadata, dict):
        return f"{metadata.get('page_start')}-{metadata.get('page_end')}"
    return "?"


def main() -> None:
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    OUT_DIR.mkdir(exist_ok=True)
    count = 0
    for task in read_jsonl(ROOT / "pool.jsonl"):
        lines = [
            f"题目编号: {task['query_id']}",
            f"问题: {task['question']}",
            f"类型: {task['question_type']} | 难度: {task['difficulty']} | 语言: {task['language']} | 有答案: {task['answerable']} | 分区: {task['split']}",
            "",
            "必须覆盖的事实:",
        ]
        for fact in task.get("required_facts") or []:
            lines.append(f"  - {fact}")
        lines += [
            "",
            f"参考答案: {task.get('reference_answer') or '(无)'}",
            "",
            "-" * 64,
            f"待判断块共 {task['pool_size']} 个，逐个给出 0/1/2/3 等级。",
            "",
        ]
        for i, entry in enumerate(task["pool"], start=1):
            doc = corpus[entry["doc_id"]]
            lines.append(
                f"[{i}] doc_id={entry['doc_id']} | 来源={doc['source_id']} | 页={pages(doc.get('metadata'))} | 标题={doc['title']}"
            )
            lines.append(wrap(doc["text"]))
            lines.append("")
        (OUT_DIR / f"{task['query_id']}.txt").write_text("\n".join(lines), encoding="utf-8")
        count += 1
    print(f"生成 {count} 个任务文件 -> {OUT_DIR}")


if __name__ == "__main__":
    main()
