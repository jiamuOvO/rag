# -*- coding: utf-8 -*-
"""Run tests/eval_cases.yaml against a RUNNING service, so the model is really called.

`rag.cli evaluate` builds a Pipeline in-process and therefore needs RAG_CHAT_API_KEY /
RAG_EMBEDDING_API_KEY in the current environment.  Those keys are only ever entered
interactively by scripts/start.ps1 and are never written to disk, so any shell other than the
one that started the service reports EMBEDDING_CONFIG_MISSING,CHAT_CONFIG_MISSING and silently
scores a degraded, extractive-only run.  Posting to the already-running service sidesteps that:
the service holds the keys.

Usage:
    PYTHONPATH=src .venv/Scripts/python.exe tests/evaluate_http.py
    PYTHONPATH=src .venv/Scripts/python.exe tests/evaluate_http.py --json var/eval_http.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import yaml

REAL_MODES = {"openai_compatible", "ollama", "anthropic", "openai"}


def post(base_url: str, path: str, payload: dict, timeout: float) -> tuple[int, dict]:
    request = urllib.request.Request(
        base_url.rstrip("/") + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def get(base_url: str, path: str, timeout: float = 20.0):
    with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("tests/eval_cases.yaml"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=200.0)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    raw = yaml.safe_load(args.cases.read_text(encoding="utf-8")) or {}
    cases = raw.get("cases", [])
    # Same scope mapping rag.cli evaluate does, so a paper_names case is scoped identically.
    try:
        listing = get(args.base_url, "/v1/papers?limit=200")
        papers = {item["file_name"]: item["paper_id"] for item in listing["items"]}
    except Exception as exc:
        print(f"! could not list papers ({exc}); paper_names cases will run unscoped")
        papers = {}
    print(f"{len(cases)} cases -> {args.base_url} (real model calls)\n")

    results = []
    for case in cases:
        scope = [papers[name] for name in case.get("paper_names", []) if name in papers] or None
        payload = {"question": case["question"], "top_k": int(case.get("top_k", 5)),
                   "paper_ids": scope,
                   "answer_policy": case.get("answer_policy", "evidence_first"),
                   "retrieval_depth": case.get("retrieval_depth", "standard")}
        started = time.perf_counter()
        row = {"id": case["id"], "question": case["question"], "http": None, "error": None}
        try:
            row["http"], body = post(args.base_url, "/v1/query", payload, args.timeout)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["passed"] = False
            results.append(row)
            print(f"  {case['id']:<30} ERROR {row['error']}")
            continue
        row["elapsed_s"] = round(time.perf_counter() - started, 1)
        row["request_id"] = body.get("request_id")
        row["answer_mode"] = body.get("answer_mode")
        row["degraded"] = body.get("degraded")
        row["reason"] = body.get("degradation_reason")
        row["evidence_count"] = len(body.get("evidence") or [])
        row["timings_ms"] = body.get("timings_ms")

        evidence_text = " ".join((item.get("excerpt") or "").lower()
                                 for item in body.get("evidence") or [])
        expected = case.get("expected_mode", "evidence")
        checks = {
            "mode": bool(body.get("insufficient_evidence")) if expected == "refusal"
                    else bool(body.get("evidence")),
            "terms": all(str(t).lower() in evidence_text for t in case.get("required_terms", [])),
            "scope": not scope or all(item.get("paper_id") in scope
                                      for item in body.get("evidence") or []),
            "citations": all((item.get("evidence_id") or "").startswith("ev_")
                             for item in body.get("evidence") or []),
            # a run that never reached the model is not a passing run, even when the checks hold
            "not_degraded": not body.get("degraded"),
        }
        row["checks"] = checks
        row["passed"] = all(checks.values())
        results.append(row)
        print(f"  {case['id']:<30} http={row['http']} {row['elapsed_s']:>6}s "
              f"mode={row['answer_mode']:<18} degraded={str(row['degraded']):<5} "
              f"ev={row['evidence_count']} passed={row['passed']}")

    passed = sum(1 for r in results if r["passed"])
    real = sum(1 for r in results if r.get("answer_mode") in REAL_MODES)
    print(f"\npassed {passed}/{len(results)}   real model generation {real}/{len(results)}   "
          f"degraded {sum(1 for r in results if r.get('degraded'))}/{len(results)}")
    for row in results:
        if not row["passed"] and row.get("checks"):
            failed = [k for k, v in row["checks"].items() if not v]
            print(f"  ! {row['id']}: failed {failed} ({row.get('reason')})")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "transport": "http", "base_url": args.base_url, "case_file": str(args.cases),
            "total": len(results), "passed": passed, "real_generation": real,
            "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
