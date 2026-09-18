"""Create an isolated trial run: a fixed block list split into disjoint tasks.

Every run gets its own directory under trial/runs/<run_id>/ so that a late write from an
earlier, uncancellable task can never collide with a new attempt. Nothing is ever read back
from an unlisted path.

Usage:
    python -B tests/biomass_furan/make_trial_run.py q_0017 --from-batch 4 --blocks 10 --split 5
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LINE_CAP = 1800


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def wrap(text: str, cap: int = LINE_CAP) -> str:
    flat = " ".join(text.split())
    return "\n".join(flat[i:i + cap] for i in range(0, len(flat), cap)) if flat else "(空块)"


def arg(name: str, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main() -> None:
    qid = sys.argv[1] if len(sys.argv) > 1 else "q_0017"
    from_batch = int(arg("--from-batch", 4))
    n_blocks = int(arg("--blocks", 10))
    split = int(arg("--split", 5))

    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    index = json.loads((ROOT / "trial" / qid / "_batch_index.json").read_text(encoding="utf-8"))
    source_docs = next(b["docs"] for b in index["batches"] if b["n"] == from_batch)[:n_blocks]

    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_dir = ROOT / "trial" / "runs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    q = queries[qid]
    tasks = [source_docs[i:i + split] for i in range(0, len(source_docs), split)]
    manifest = {"run_id": run_id, "query_id": qid, "created_at": datetime.now(timezone.utc).isoformat(),
                "source": f"batch_{from_batch:02d}[0:{n_blocks}]", "block_count": len(source_docs),
                "tasks": []}

    for i, docs in enumerate(tasks):
        label = chr(ord("A") + i)
        lines = [
            f"题目编号: {qid}",
            f"问题: {q['question']}",
            f"类型: {q['question_type']} | 难度: {q['difficulty']} | 语言: {q['language']} | 有答案: {q['answerable']}",
            "",
            "必须覆盖的事实:",
        ]
        for fact in q.get("required_facts") or []:
            lines.append(f"  - {fact}")
        lines += ["", f"参考答案: {q.get('reference_answer') or '(无)'}"]
        if q.get("source_group"):
            lines += ["", f"题目指定来源: {q['source_group']}"]
        lines += ["", "=" * 64,
                  f"本任务共 {len(docs)} 个块（试验 run {run_id} / 任务 {label}）。逐个给出 0/1/2/3 等级。",
                  "判断依据只能是本块正文与本任务给出的题目要求。", "=" * 64, ""]
        for j, did in enumerate(docs, start=1):
            doc = corpus[did]
            lines.append(f"[{j}] doc_id={did} | 来源={doc['source_id']} | 标题={doc['title']}")
            lines.append(wrap(doc["text"]))
            lines.append("")
        (out_dir / f"task_{label}.txt").write_text("\n".join(lines), encoding="utf-8")
        manifest["tasks"].append({"label": label, "input": f"task_{label}.txt",
                                  "output": f"task_{label}.json", "docs": docs})

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    print(f"run_id: {run_id}")
    print(f"目录: {out_dir}")
    for t in manifest["tasks"]:
        print(f"  任务 {t['label']}: {len(t['docs'])} 块 -> {t['output']}")
    print(f"创建时刻: {manifest['created_at']}")


if __name__ == "__main__":
    main()
