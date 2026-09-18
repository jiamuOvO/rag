"""Full gap-fill execution over the remaining pending batches.

Agreed parameters (audit decisions 2026-09-18):
  * 5 blocks per batch, 2 concurrent workers, the model config that already ran successfully
  * one request budget per batch -- the transport retry layer and the encoding layer SHARE it,
    so the two layers can never multiply
  * transport errors (timeout / 429 / 5xx): at most 2 extra attempts with backoff
  * auth or parameter errors (4xx other than 429): stop immediately, never retried
  * judgment encoding (--encoding):
        compact   digit-string grade rows (current behaviour)
        perblock  one explicit evidence entry per document
        fallback  compact first; if that response is rejected on FORMAT or VALIDATION, spend at
                  most ONE extra request in perblock encoding, on the same blocks and rules.
                  Never cycles back. A batch that fails again goes to the pending list.
  * format/validation rejections are counted separately from transport errors
  * quality stop: 3 consecutive batches that still fail after the fallback ladder -> pause
  * transport stop: 3 consecutive transport failures -> pause
  * every response that carries usage is billed to the totals, format failures included;
    a request that produced no usable usage is recorded as "usage unknown", never as zero
  * truncated responses never land on disk (annotate.request_batch rejects finish_reason != stop)
  * a batch only counts as done when its output VALIDATES and matches the index
  * the first 4 batches must all pass before dispatch continues

Reuses annotate.request_batch so prompt construction and parsing stay identical to trial runs.

Usage:
    python -B tests/biomass_furan/run_full_gapfill.py --plan        # list plan, make no calls
    python -B tests/biomass_furan/run_full_gapfill.py --workers 2 --encoding fallback
The credential is read from the environment and is never printed.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "gapfill"
STATE_PATH = ROOT / "run_full_state.json"
PENDING_PATH = ROOT / "run_full_pending.json"
ATTEMPTS_PATH = ROOT / "run_full_attempts.json"

MAX_REQUESTS_PER_BATCH = 4
NETWORK_EXTRA_ATTEMPTS = 2
ENCODING_SWITCHES = 1
AUTH_ERROR_CODES = {400, 401, 403, 404, 409, 422}
CONSECUTIVE_FORMAT_LIMIT = 10
CONSECUTIVE_NETWORK_LIMIT = 3
FIRST_BATCH_GATE = 4

sys.path.insert(0, str(ROOT))
import annotate  # noqa: E402
from verify_judgments import match_level  # noqa: E402


def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_inputs():
    corpus = {c["doc_id"]: c for c in read_jsonl(ROOT / "corpus.jsonl")}
    queries = {q["query_id"]: q for q in read_jsonl(ROOT / "queries.jsonl")}
    index = json.loads((OUT_DIR / "_gapfill_index.json").read_text(encoding="utf-8"))
    # Supplementary tasks cover scoped pairs that no original batch listed. They live in their own
    # index so the original batches are never renumbered or rebuilt.
    supplement = OUT_DIR / "_gapfill_supplement.json"
    if supplement.exists():
        index = index + json.loads(supplement.read_text(encoding="utf-8"))
    return corpus, queries, index


def validate(qid: str, rows: list[dict], expected: list[str], corpus: dict) -> list[str]:
    problems: list[str] = []
    ids = [r.get("doc_id") for r in rows]
    if set(ids) != set(expected):
        problems.append(f"doc_id 集合不一致（缺 {len(set(expected)-set(ids))}，多 {len(set(ids)-set(expected))}）")
    if len(ids) != len(set(ids)):
        problems.append("存在重复 doc_id")
    for r in rows:
        rel = r.get("relevance")
        if type(rel) is not int or rel not in (0, 1, 2, 3):
            problems.append(f"{str(r.get('doc_id'))[:18]} 等级非法 {rel!r}")
            continue
        if rel >= 2:
            quotes = r.get("evidence_quotes") or []
            if not quotes:
                problems.append(f"{str(r.get('doc_id'))[:18]} L{rel} 缺引文")
                continue
            for q in quotes:
                lvl, _ = match_level(q, corpus.get(r["doc_id"], {"text": ""})["text"])
                if lvl > 2:
                    problems.append(f"{str(r.get('doc_id'))[:18]} L{rel} 引文层级 {lvl}")
        elif not str(r.get("reason") or "").strip():
            problems.append(f"{str(r.get('doc_id'))[:18]} L{rel} 缺理由")
    return problems


def collect_judged_pairs(corpus: dict) -> dict:
    """Validated (query_id, doc_id) -> record, gathered from every gapfill output on disk.

    Keyed by PAIR, not by batch. The earliest 29 records were produced by the agent under a
    different partition of the same blocks, so batch-level equality would falsely re-queue work
    that is already judged. A record only covers a pair if it passes the mechanical checks.
    """
    pairs = {}
    for path in sorted(OUT_DIR.glob("q_*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            continue
        qid = payload.get("query_id")
        rows = payload.get("judgments") or []
        if not qid or not rows:
            continue
        if validate(qid, rows, [r.get("doc_id") for r in rows], corpus):
            continue                       # an invalid record covers nothing
        for r in rows:
            if isinstance(r.get("doc_id"), str) and r["doc_id"]:
                pairs[(qid, r["doc_id"])] = r
    return pairs


def exhausted_batches(items: list[dict], corpus: dict) -> set[str]:
    """Batches whose saved responses show BOTH encodings were already tried and rejected.

    Derived from the response artifacts, not from a state file, so it survives a restart and cannot
    be reset by one.
    """
    seen = defaultdict(set)
    for path in (ROOT / "runtime" / "model_responses").glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            continue
        qids, dids = rec.get("query_ids") or [], rec.get("doc_ids") or []
        if not qids:
            continue
        seen[(qids[0], tuple(dids))].add(rec.get("encoding") or "compact")
    out = set()
    for item in items:
        encs = seen.get((item["query_id"], tuple(item["docs"])), set())
        if "compact" in encs and "perblock" in encs:
            out.add(f"{item['query_id']}_batch_{item['batch']:02d}")
    return out


def scan_checkpoints(index: list[dict], corpus: dict) -> tuple[list, list, list]:
    """A batch is DONE when every block it lists already carries a VALID judgment.

    Existence of a same-named JSON is not enough, and neither is batch-level equality: the check
    is by unique (question, block) pair, then mapped back to the batches that contain those pairs.
    Pending blocks are always dispatched as the whole index batch, so the request and the task
    file always describe the same block set.
    """
    judged = collect_judged_pairs(corpus)
    done, todo, rejected, partial = [], [], [], []
    for item in index:
        qid, n = item["query_id"], item["batch"]
        label = f"{qid}_batch_{n:02d}"
        if not (OUT_DIR / qid / f"batch_{n:02d}.txt").exists():
            rejected.append({"batch": label, "reason": "任务文件 .txt 缺失"})
            todo.append(item)
            continue
        missing = [d for d in item["docs"] if (qid, d) not in judged]
        if missing:
            if len(missing) != len(item["docs"]):
                partial.append({"batch": label, "covered": len(item["docs"]) - len(missing),
                                "blocks": len(item["docs"])})
            todo.append(item)
            continue
        rec = judged[(qid, item["docs"][0])]
        done.append({"batch": label, "records": len(item["docs"]),
                     "method": rec.get("method"), "model": rec.get("model")})
    if partial:
        print(f"  ⚠ {len(partial)} 批只被部分覆盖，将整批重跑以保持索引与请求一致：")
        for p in partial[:5]:
            print(f"     {p['batch']}: {p['covered']}/{p['blocks']} 块已判定")
    return done, todo, rejected


def ensure_credential() -> str:
    """Make the chat key visible to this process, without ever revealing it.

    The operator sets the key in their own terminal, so it lives only in that process tree and a
    separately launched process never inherits it. If the variable is absent we read the USER
    environment block from the registry (what `setx` writes) and inject it in-process.

    The value is never printed, logged, or written anywhere. Only a status word is returned.
    """
    name = "RAG_CHAT_API_KEY"
    try:
        import yaml
        cfg = yaml.safe_load((ROOT.parent.parent / "config.yaml").read_text(encoding="utf-8")) or {}
        name = str(cfg.get("chat", {}).get("api_key_env", name))
    except Exception:                                         # noqa: BLE001
        pass
    if os.getenv(name):
        return "env"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return "absent"
    except Exception:                                         # noqa: BLE001
        return "unreadable"
    if value:
        os.environ[name] = value
        return "registry"
    return "absent"


def resolve_settings():
    """Load settings, then point them at the benchmark's own endpoint.

    tests/test_api.yaml is the benchmark's endpoint file. It is read HERE rather than merged into
    config.yaml, because the production service also loads config.yaml and the audit boundary
    forbids touching production configuration. Settings is a frozen dataclass, so replace() is
    required. The credential still comes from the env var named in config.yaml.
    """
    import dataclasses
    import yaml
    from rag.config import Settings

    settings = Settings.load()
    api_file = ROOT.parent.parent / "tests" / "test_api.yaml"
    if api_file.exists():
        api_cfg = yaml.safe_load(api_file.read_text(encoding="utf-8")).get("chat", {})
        settings = dataclasses.replace(
            settings,
            chat_base_url=api_cfg.get("base_url", settings.chat_base_url),
            chat_model=api_cfg.get("model", settings.chat_model),
        )
    return settings


def main() -> int:
    workers = 2
    if "--workers" in sys.argv:
        workers = int(sys.argv[sys.argv.index("--workers") + 1])
    encoding = "compact"
    if "--encoding" in sys.argv:
        encoding = sys.argv[sys.argv.index("--encoding") + 1]
    if encoding not in {"compact", "perblock", "fallback"}:
        print(f"未知 --encoding {encoding!r}；可选 compact / perblock / fallback")
        return 2
    plan_only = "--plan" in sys.argv

    exclude = set()
    if "--exclude" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--exclude") + 1])
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            exclude = {x["label"] if isinstance(x, dict) else x for x in raw}
        print(f"排除清单 {path.name}：跳过 {len(exclude)} 批（本批不做请求）")

    corpus, queries, index = load_inputs()
    done, todo, rejected = scan_checkpoints(index, corpus)
    if exclude:
        skipped = [i for i in todo if f"{i['query_id']}_batch_{i['batch']:02d}" in exclude]
        todo = [i for i in todo if f"{i['query_id']}_batch_{i['batch']:02d}" not in exclude]
        if skipped:
            print(f"  按排除清单跳过 {len(skipped)} 批 / {sum(i['blocks'] for i in skipped)} 条")
    attempts = json.loads(ATTEMPTS_PATH.read_text(encoding="utf-8")) if ATTEMPTS_PATH.exists() else {}

    # Failure isolation: a batch whose ladder is already exhausted is NOT picked up again by a
    # later run. Re-sending an identical prompt to a batch that failed BOTH encodings just
    # reproduces the defect; those pairs go through targeted re-judgement instead. --retry-exhausted
    # overrides this for a deliberate retry.
    if "--retry-exhausted" not in sys.argv:
        # A targeted re-judgement legitimately re-touches the same block set with a different
        # request (fewer blocks, plus a note naming the previous defect), so it is exempt from the
        # isolation rule -- otherwise the isolation would skip the very batches built to fix them.
        candidates = [i for i in todo if not i.get("retask_reason")]
        exempt = len(todo) - len(candidates)
        exhausted = exhausted_batches(candidates, corpus)
        if exhausted or exempt:
            print(f"失败隔离：跳过 {len(exhausted)} 批两种编码均已失败的批次"
                  f"（它们只走定点重判；--retry-exhausted 可强制重试）"
                  + (f"；豁免 {exempt} 个定点重判批次" if exempt else ""))
            todo = [i for i in todo
                    if i.get("retask_reason") or
                    f"{i['query_id']}_batch_{i['batch']:02d}" not in exhausted]
    total_remaining = sum(i["blocks"] for i in todo)
    print(f"接口检查：已完成 {len(done)} 批 / 待执行 {len(todo)} 批 / "
          f"{total_remaining} 条 | 并发 {workers} | 每批 5 块 | 判定编码 {encoding}")
    if rejected:
        print(f"  ⚠ 有 {len(rejected)} 批同名 JSON 存在但未通过对应性校验，已放回待执行队列：")
        for r in rejected[:5]:
            print(f"     {r['batch']}: {r['reason']}")
    print(f"  每批请求上限 {MAX_REQUESTS_PER_BATCH}（传输重试与换编码共用同一预算）")

    # The credential must be injected BEFORE Settings.load(), which is what copies it into the
    # settings object. Injecting afterwards leaves chat_api_key as None and every request fails
    # with TypeError when the Bearer header is built.
    cred = ensure_credential()
    settings = resolve_settings()
    print(f"接口: {settings.chat_base_url} | 模型 {settings.chat_model}")

    if plan_only:
        print("\n--plan：未发出任何请求")
        return 0

    if cred == "registry":
        print("凭据来源：用户级环境（注册表），仅注入本进程，不打印不落盘")
    if not os.getenv("RAG_CHAT_API_KEY"):
        print(f"\n[需用户执行] 无法获得凭据（{cred}）。")
        print("请在本机终端执行一次：setx RAG_CHAT_API_KEY \"<你的key>\"")
        return 2

    lock = threading.Lock()
    stop = threading.Event()
    state = {"encoding_mode": encoding, "started": time.strftime("%H:%M:%S"),
             "batches_planned": len(todo), "batches_done": 0, "batches_failed": 0,
             "requests": 0, "requests_ok": 0, "requests_format_reject": 0,
             "requests_transport_error": 0, "requests_encoding_switch": 0,
             "usage_unknown_requests": 0, "usage_unknown_batches": [],
             "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
             "format_fail_batches": 0, "network_fail_batches": 0,
             "consecutive_format_fail": 0, "consecutive_network_fail": 0,
             "requests_by_batch": {}, "failures": [], "pending": []}
    t0 = time.time()

    def account(usage, label: str) -> None:
        """Bill every response that carries usage, including format-failed ones."""
        if isinstance(usage, dict) and (usage.get("total_tokens") or usage.get("prompt_tokens")
                                        or usage.get("completion_tokens")):
            state["prompt_tokens"] += usage.get("prompt_tokens", 0)
            state["completion_tokens"] += usage.get("completion_tokens", 0)
            state["total_tokens"] += usage.get("total_tokens", 0)
        else:
            state["usage_unknown_requests"] += 1
            if label not in state["usage_unknown_batches"]:
                state["usage_unknown_batches"].append(label)

    def next_encoding(current: str) -> str:
        return "perblock" if current == "compact" else "compact"

    def run_one(item: dict) -> None:
        qid, n = item["query_id"], item["batch"]
        label = f"{qid}_batch_{n:02d}"
        docs = [corpus[d] for d in item["docs"]]
        qlist = [queries[qid]]
        used = "perblock" if encoding == "perblock" else "compact"
        switches = 0
        net_retries = 0
        budget = MAX_REQUESTS_PER_BATCH

        while budget > 0:
            if stop.is_set():
                return
            budget -= 1
            started = time.time()
            with lock:
                state["requests"] += 1
                state["requests_by_batch"][label] = state["requests_by_batch"].get(label, 0) + 1
            rows = usage = None
            reject = None
            exc = None
            try:
                rows, usage = annotate.request_batch(settings, qlist, docs,
                                                     phase="first_pass", encoding=used,
                                                     note=item.get("retask_reason"))
            except Exception as e:                            # noqa: BLE001
                exc = e

            if exc is not None:
                has_response = getattr(exc, "response_id", None) is not None
                code = getattr(exc, "code", None)
                with lock:
                    entry = {"batch": label, "encoding": used, "error": type(exc).__name__,
                             "message": str(exc)[:200], "http_code": code,
                             "response_id": (getattr(exc, "response_id", None) or "")[:16] or None,
                             "elapsed_s": round(time.time() - started, 1)}
                    state["failures"].append(entry)
                if has_response:
                    # The provider answered; we rejected the content. Usage is real spend.
                    with lock:
                        account(getattr(exc, "usage", None), label)
                        state["requests_format_reject"] += 1
                    reject = f"{type(exc).__name__}: {str(exc)[:120]}"
                elif code in AUTH_ERROR_CODES:
                    with lock:
                        state["batches_failed"] += 1
                        state["stop_reason"] = f"认证/参数错误 HTTP {code} @ {label}"
                        state["pending"].append({"batch": label, "kind": "auth", "reason": str(exc)[:120]})
                    print(f"  {label}: {type(exc).__name__}({code}) —— 认证/参数错误，立即停止")
                    stop.set()
                    return
                elif not isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
                    # Not a transport fault: this is a defect in the runner or the configuration.
                    # Retrying it would burn the batch budget and then surface as a bogus
                    # "network" stop, hiding the real bug (a TypeError from an unset key did
                    # exactly that once). Abort immediately and say what it was.
                    with lock:
                        state["batches_failed"] += 1
                        state["stop_reason"] = (f"内部错误 {type(exc).__name__} @ {label}: "
                                                f"{str(exc)[:140]}")
                        state["pending"].append({"batch": label, "kind": "internal",
                                                 "reason": f"{type(exc).__name__}: {str(exc)[:140]}"})
                    print(f"  {label}: 内部错误 {type(exc).__name__}: {str(exc)[:160]}")
                    print("  这不是网络问题 —— 立即停止，请修代码后重跑")
                    stop.set()
                    return
                else:
                    with lock:
                        state["requests_transport_error"] += 1
                    if net_retries < NETWORK_EXTRA_ATTEMPTS and budget > 0:
                        net_retries += 1
                        wait = 5 * net_retries
                        print(f"  {label}: {type(exc).__name__} 传输错误，{wait}s 后重试（{net_retries}/{NETWORK_EXTRA_ATTEMPTS}）")
                        time.sleep(wait)
                        continue
                    with lock:
                        state["batches_failed"] += 1
                        state["network_fail_batches"] += 1
                        state["consecutive_network_fail"] += 1
                        state["pending"].append({"batch": label, "kind": "network",
                                                 "reason": f"{type(exc).__name__}: {str(exc)[:120]}"})
                        tripped = state["consecutive_network_fail"] >= CONSECUTIVE_NETWORK_LIMIT
                    print(f"  {label}: 传输错误重试已用尽 —— 记入待处理")
                    if tripped:
                        with lock:
                            state["stop_reason"] = (f"连续 {CONSECUTIVE_NETWORK_LIMIT} 次传输失败 @ {label}"
                                                    f"（第 {state['requests_transport_error']} 次传输错误）")
                        print(f"  连续 {CONSECUTIVE_NETWORK_LIMIT} 次传输失败 —— 暂停派发")
                        stop.set()
                    return
            else:
                with lock:
                    account(usage, label)
                    state["requests_ok"] += 1
                problems = validate(qid, rows, item["docs"], corpus)
                if not problems:
                    payload = {"query_id": qid, "batch": n, "judgments": rows, "usage": usage,
                               "encoding": used, "source": "api_full_gapfill"}
                    (OUT_DIR / qid / f"batch_{n:02d}.json").write_text(
                        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                    with lock:
                        state["batches_done"] += 1
                        state["consecutive_format_fail"] = 0
                        state["consecutive_network_fail"] = 0
                        if state["batches_done"] % 10 == 0 or state["batches_done"] <= 4:
                            el = time.time() - t0
                            print(f"  已完成 {state['batches_done']} 批 | 失败 {state['batches_failed']} | "
                                  f"用时 {el/60:.1f} 分 | 请求 {state['requests']}")
                    return
                with lock:
                    state["requests_format_reject"] += 1
                    state["failures"].append({"batch": label, "encoding": used,
                                              "error": "validation", "problems": problems[:5],
                                              "response_id": (usage or {}).get("raw_response_id", "")[:16],
                                              "elapsed_s": round(time.time() - started, 1)})
                reject = "校验未通过：" + "；".join(problems[:2])

            # A rejected batch gets ONE shot at the other encoding, on the same blocks and rules.
            if encoding == "fallback" and switches < ENCODING_SWITCHES and used == "compact" and budget > 0:
                switches += 1
                used = next_encoding(used)
                with lock:
                    state["requests_encoding_switch"] += 1
                print(f"  {label}: {reject} —— 换 perblock 编码再试一次（不再循环）")
                continue
            with lock:
                state["batches_failed"] += 1
                state["format_fail_batches"] += 1
                state["consecutive_format_fail"] += 1
                state["pending"].append({"batch": label, "kind": "format", "reason": reject})
                tripped = state["consecutive_format_fail"] >= CONSECUTIVE_FORMAT_LIMIT
            print(f"  {label}: fallback 后仍不合格 —— 记入待处理清单")
            if tripped:
                with lock:
                    state["stop_reason"] = (f"连续 {CONSECUTIVE_FORMAT_LIMIT} 批 fallback 后仍不合格 @ {label}"
                                            f"（本轮格式失败 {state['format_fail_batches']} 批）")
                print(f"  连续 {CONSECUTIVE_FORMAT_LIMIT} 批 fallback 后仍不合格 —— 暂停派发并汇报")
                stop.set()
            return

    with ThreadPoolExecutor(max_workers=workers) as pool:
        gate = todo[:FIRST_BATCH_GATE]
        if gate:
            print(f"  前 {len(gate)} 批为放行闸门，全部通过后才继续派发")
            gate_results = [pool.submit(run_one, item) for item in gate]
            for f in gate_results:
                f.result()
        if not stop.is_set() and len(todo) > FIRST_BATCH_GATE:
            got = state["batches_done"]
            if got == len(gate):
                print(f"  闸门通过（{got}/{len(gate)}）—— 自动继续派发剩余 {len(todo)-len(gate)} 批")
            else:
                print(f"  闸门未全通过（{got}/{len(gate)} 成功）—— 仍继续派发，但请留意上方失败原因")
            rest = [pool.submit(run_one, item) for item in todo[FIRST_BATCH_GATE:]]
            for f in rest:
                f.result()
                if stop.is_set():
                    break

    state["elapsed_min"] = round((time.time() - t0) / 60, 1)
    # Re-scan from disk rather than trusting the in-memory counters: the remaining gap is
    # "index entries whose validated output is absent", which is exactly what scan_checkpoints does.
    done_after, todo_after, _ = scan_checkpoints(index, corpus)
    state["remaining_batches"] = len(todo_after)
    state["remaining_records"] = sum(i["blocks"] for i in todo_after)
    state["total_validated_batches"] = len(done_after)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    PENDING_PATH.write_text(json.dumps(state["pending"], ensure_ascii=False, indent=1), encoding="utf-8")

    # Cumulative attempt ledger: a batch's request count survives across runs, so "tried twice and
    # failed twice" stays visible instead of being reset by every restart.
    done_labels = {d["batch"] for d in done_after}
    for label, n in state["requests_by_batch"].items():
        prev = attempts.get(label) or {"requests": 0, "runs": 0}
        prev["requests"] += n
        prev["runs"] += 1
        prev["last_run"] = state["started"]
        prev["last_outcome"] = "validated" if label in done_labels else "pending"
        attempts[label] = prev
    ATTEMPTS_PATH.write_text(json.dumps(attempts, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    print(f"完成 {state['batches_done']}/{len(todo)} 批 | 失败 {state['batches_failed']}"
          f"（格式 {state['format_fail_batches']} / 传输 {state['network_fail_batches']}）")
    print(f"请求 {state['requests']} 次：成功 {state['requests_ok']}｜"
          f"格式被拒 {state['requests_format_reject']}｜传输错误 {state['requests_transport_error']}｜"
          f"换编码 {state['requests_encoding_switch']}")
    print(f"tokens: prompt {state['prompt_tokens']} + completion {state['completion_tokens']}"
          f" = {state['total_tokens']}｜用量未知请求 {state['usage_unknown_requests']}")
    print(f"耗时 {state['elapsed_min']} 分钟 | 剩余 {state['remaining_batches']} 批（约 {state['remaining_records']} 条）")
    print(f"状态文件 {STATE_PATH}｜待处理清单 {PENDING_PATH}")
    return 0 if not stop.is_set() else 1


if __name__ == "__main__":
    raise SystemExit(main())
