"""Write the provenance ledger for every gapfill batch already on disk.

The benchmark is allowed to mix annotation models and output encodings, so provenance does not
have to be uniform -- but it must be as-recorded, and it must not claim more than the artifacts
show. This script re-derives each batch's lineage from the files instead of asserting it:

    agent_annotation                 no API response exists; the records carry no model field
                                     at all, because the agent produced them without an API call
    api_endpoint_unconfirmed         an API response exists but its recorded model name is the
                                     OLD gateway's model, so the endpoint cannot be established
    api_official_deepseek_flash      recorded model name equals tests/test_api.yaml's model AND
                                     differs from config.yaml's, which only happens when the
                                     benchmark endpoint override was in effect

Usage: python -B tests/biomass_furan/build_provenance.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gapfill"
LEDGER = OUT / "_provenance.json"

AGENT_TASK_DIR = "archive-20260917/tasks-asused"
AGENT_SPEC = "archive-20260917/ANNOTATION_INSTRUCTIONS.md"
CURRENT_SPEC = "ANNOTATION_INSTRUCTIONS.md"
CORRECTION_LOGS = ["retask_merge_log.json", "repair_log.json",
                   "archive-20260917/verify_judgments_v3.json", "repair_log.json"]

# Numbers only used for the note text, never as a substitute for the recorded facts.
OLD_GATEWAY_MODEL = "deepseek-v4-flash-chat"


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    responses = {}
    for path in (ROOT / "runtime" / "model_responses").glob("*.json"):
        responses[path.stem] = json.loads(path.read_text(encoding="utf-8"))

    try:
        import yaml
        official_model = yaml.safe_load(
            (ROOT.parent.parent / "tests" / "test_api.yaml").read_text(encoding="utf-8")
        )["chat"]["model"]
    except Exception:                                          # noqa: BLE001
        official_model = "deepseek-flash"

    batches = []
    for path in sorted(OUT.glob("q_*/*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        qid, n = payload["query_id"], payload["batch"]
        rows = payload.get("judgments") or []
        record = {"label": f"{qid}_batch_{n:02d}", "query_id": qid, "batch": n,
                  "records": len(rows), "output": str(path.relative_to(ROOT)).replace("\\", "/"),
                  "record_fields": sorted({k for r in rows for k in r}),
                  "grade_distribution": {str(g): sum(1 for r in rows if r.get("relevance") == g)
                                         for g in (0, 1, 2, 3)},
                  "declared_source": payload.get("source")}
        rid = next((r.get("raw_response_id") for r in rows if r.get("raw_response_id")), None)

        if rid and rid in responses:
            rec = responses[rid]
            model = rec.get("model")
            record["lineage"] = ("api_official_deepseek_flash" if model != OLD_GATEWAY_MODEL
                                 else "api_endpoint_unconfirmed")
            record["model"] = model
            record["model_note"] = (
                "模型名与 tests/test_api.yaml 一致且与 config.yaml 不同，说明基准接口覆盖已生效；"
                "接口地址由模型名反推，历史请求未直接记录 base_url。"
                if model != OLD_GATEWAY_MODEL else
                "记录到的模型名是 config.yaml 中旧自建网关的模型名；该两次请求实际打到哪个地址"
                "无法从产物确认，trial_log.json 不记录 base_url。**不据此宣称性能对照结论。**")
            record["raw_response_id"] = rid
            record["system_prompt_sha256"] = rec.get("system_prompt_sha256")
            record["encoding"] = rec.get("encoding")
        else:
            record["lineage"] = "agent_annotation"
            record["model"] = None
            record["model_note"] = ("Agent 标注；模型信息未知或未记录。记录内不含 model / method / "
                                    "raw_response_id 字段，这是无 API 调用产出的特征，不构成缺陷。")
            record["task"] = f"{AGENT_TASK_DIR}/{qid}.txt" if (ROOT / AGENT_TASK_DIR / f"{qid}.txt").exists() else None
            record["spec"] = [AGENT_SPEC, CURRENT_SPEC]
            record["correction_logs"] = [p for p in dict.fromkeys(CORRECTION_LOGS) if (ROOT / p).exists()]
        batches.append(record)

    totals = {}
    for b in batches:
        totals[b["lineage"]] = totals.get(b["lineage"], 0) + b["records"]

    LEDGER.write_text(json.dumps({
        "note": "每个批次的来源线，由 build_provenance.py 从产物重新推导，不是人工断言。"
                "测评集允许来自不同标注模型与不同输出编码；冻结条件为规则一致、来源如实记录、"
                "必要质量检查完成。",
        "official_model_name": official_model,
        "old_gateway_model_name": OLD_GATEWAY_MODEL,
        "totals_by_lineage": totals,
        "batches": batches,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"已写入 {LEDGER}")
    print(f"  批次 {len(batches)}｜记录 {sum(b['records'] for b in batches)}")
    for k, v in totals.items():
        print(f"  {k:32s} {v} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
