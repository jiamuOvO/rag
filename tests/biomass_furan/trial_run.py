"""Serial 10-record trial against the configured chat endpoint.

Hard limits for this run (per the agreed plan):
  * 2 batches x 5 records, STRICTLY SERIAL
  * at most 1 retry per batch
  * at most 4 requests total (retries count)
  * max_tokens per request capped at 4096
  * request timeout 180 s
  * auth / parameter errors -> stop and report, do NOT retry
  * never prints the credential

Reuses annotate.request_batch (the direct-call path, not the local bridge) so prompt construction
and response parsing stay identical to the rest of the pipeline.

Usage:
    set RAG_CHAT_API_KEY in the environment first (locally; never paste it into a chat)
    python -B tests/biomass_furan/trial_run.py --dry-run   # 只选批次、发 0 次请求
    python -B tests/biomass_furan/trial_run.py             # 实际执行
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "trial_api"
MAX_REQUESTS = 4
MAX_TOKENS = 4096
TIMEOUT = 180

sys.path.insert(0, str(ROOT))
import annotate  # noqa: E402


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def pick_batches() -> list[dict]:
    """Two batches of 5 from the TRUE pending list: one mostly positive re-checks, one mostly new.

    A batch counts as done only when its output JSON exists -- the index lists every planned batch,
    including ones already completed.
    """
    index = json.loads((ROOT / "gapfill" / "_gapfill_index.json").read_text(encoding="utf-8"))
    pending = [
        i for i in index
        if i["blocks"] == 5
        and not (ROOT / "gapfill" / i["query_id"] / f"batch_{i['batch']:02d}.json").exists()
    ]
    with_pos = [i for i in pending if i["recheck_pos"] or i["recheck_l1"]]
    with_new = [i for i in pending if i["new"]]
    chosen: list[dict] = []
    if with_pos:
        chosen.append(with_pos[0])
    for cand in with_new:
        if cand not in chosen:
            chosen.append(cand)
            break
    return chosen[:2]


def main() -> int:
    dry = "--dry-run" in sys.argv
    batches = pick_batches()
    if len(batches) < 2:
        print("无法选出两个批次，停止")
        return 1

    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}

    print("选定批次:")
    for b in batches:
        print(f"  {b['query_id']} batch_{b['batch']:02d}  {b['blocks']} 条 "
              f"(正例复核 {b['recheck_pos']} / 1级复核 {b['recheck_l1']} / 待标 {b['new']})")
    print(f"计划请求数: 2（+ 最多 2 次重试，总计 ≤ {MAX_REQUESTS}）| max_tokens={MAX_TOKENS} | 超时 {TIMEOUT}s")

    if dry:
        print("\n--dry-run：未发出任何请求")
        return 0

    if not os.getenv("RAG_CHAT_API_KEY"):
        print("\n缺少凭据：环境变量 RAG_CHAT_API_KEY 未设置。")
        print("请在本地项目终端设置后再运行；不要把密钥贴进对话。")
        return 2

    from rag.config import Settings
    settings = Settings.load()
    OUT.mkdir(exist_ok=True)

    log: list[dict] = []
    requests_made = 0
    t0 = time.time()

    for b in batches:
        qid, n = b["query_id"], b["batch"]
        docs = [corpus[d] for d in b["docs"]]
        qs = [queries[qid]]
        label = f"{qid}_batch_{n:02d}"
        attempt = 0
        while attempt < 2 and requests_made < MAX_REQUESTS:
            attempt += 1
            requests_made += 1
            started = time.time()
            try:
                rows, usage = annotate.request_batch(settings, qs, docs, phase="first_pass")
                payload = {"query_id": qid, "batch": n, "judgments": rows, "usage": usage}
                (OUT / f"{label}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                log.append({"label": label, "status": "ok", "attempt": attempt,
                            "elapsed_s": round(time.time() - started, 1), "usage": usage})
                print(f"  {label}: ok  用时 {time.time()-started:.1f}s  用量 {usage}")
                break
            except Exception as exc:                      # noqa: BLE001
                kind = type(exc).__name__
                code = getattr(exc, "code", None)
                log.append({"label": label, "status": "error", "attempt": attempt,
                            "error_type": kind, "http_code": code,
                            "elapsed_s": round(time.time() - started, 1)})
                print(f"  {label}: {kind} (code={code}) 第 {attempt} 次")
                if code in (400, 401, 403, 404, 422):
                    print("  认证或参数错误 —— 按规则停止，不重试")
                    (OUT / "trial_log.json").write_text(
                        json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
                    return 3
                if code == 429:
                    time.sleep(5)
        else:
            continue

    (OUT / "trial_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    total = time.time() - t0
    ok = sum(1 for e in log if e["status"] == "ok")
    print(f"\n完成 {ok}/2 批 | 请求 {requests_made} 次 | 总耗时 {total:.1f}s")
    print(f"日志 {OUT/'trial_log.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
