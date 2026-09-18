"""Emit the audit note for q_0013 / q_0016: question, required facts, and the re-judged blocks."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TARGETS = {
    "q_0013": [
        "chk_4f283b17046ca64f70a75f0f", "chk_8c43d6189bfd58b10083cf8d",
        "chk_d32e7ef117d91f85b89a0634", "chk_b4a63245687199605240fc88",
        "chk_8415a4ce30d60e523ec8f26f", "chk_0a912b28229092da2e595f18",
        "chk_4d14c5369e8d24183d1c0860",
    ],
    "q_0016": ["chk_df3aec9b2824dc7e019e2465"],
}


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> None:
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    pool = {p["query_id"]: p for p in read_jsonl(ROOT / "pool.jsonl")}

    out: list[str] = [
        "# 审计补充材料：q_0013 / q_0016 原块",
        "",
        "用途：供审计方判定这两题的重判是否正确。",
        "关键待决问题：**题目里指定的出处（如「2018 年综述」）是硬性限定条件，还是仅为定位提示？**",
        "",
    ]

    for qid, ids in TARGETS.items():
        q = queries[qid]
        out += ["=" * 72, f"# {qid}", "",
                f"**问题**：{q['question']}", "",
                f"类型 `{q['question_type']}` | 难度 `{q['difficulty']}` | 语言 `{q['language']}` | 分区 `{q['split']}`",
                "", "**必需事实**："]
        for fact in q.get("required_facts") or []:
            out.append(f"- {fact}")
        out += ["", f"**参考答案**：{q.get('reference_answer')}", "",
                "**题目其他线索**："]
        for key in ("domain", "source_group", "origin", "notes", "tags"):
            if q.get(key):
                out.append(f"- `{key}`: {q[key]}")

        src_count: dict[str, int] = {}
        for entry in pool[qid]["pool"]:
            sid = corpus[entry["doc_id"]]["source_id"]
            src_count[sid] = src_count.get(sid, 0) + 1
        titles = {}
        for c in corpus.values():
            titles.setdefault(c["source_id"], c["title"])

        out += ["", f"**池规模**：{pool[qid]['pool_size']} 块，来自 {len(src_count)} 个来源。占比最高的来源："]
        for sid, n in sorted(src_count.items(), key=lambda x: -x[1])[:6]:
            out.append(f"- `{sid}`（{n} 块）{titles.get(sid, '')[:70]}")

        out += ["", "---", "", "## 重判块正文", ""]
        for did in ids:
            c = corpus.get(did)
            if not c:
                out += [f"### {did}", "（不在冻结语料中）", ""]
                continue
            out += [f"### `{did}`", "",
                    f"来源 `{c['source_id']}` — {c['title'][:80]}", "",
                    "```", " ".join(c["text"].split())[:1600], "```", ""]

    (ROOT / "AUDIT_q0013_q0016.md").write_text("\n".join(out), encoding="utf-8")
    print(f"写出 AUDIT_q0013_q0016.md（{len(chr(10).join(out))} 字符）")


if __name__ == "__main__":
    main()
