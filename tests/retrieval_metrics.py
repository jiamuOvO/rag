# -*- coding: utf-8 -*-
"""Retrieval quality metrics over a gold-labelled question set.

Computes HitRate@K, Recall@K, Precision@K, MRR and nDCG@K for each retriever stage, offline by
default (BM25 only).  `--dense` additionally embeds each question once to score the dense leg and
the RRF fusion, which is the list the model actually sees.

Every number is only as good as tests/retrieval_gold.yaml.  Re-derive the labels with
tests/derive_gold.py after re-ingesting documents - chunk ids are the join key.

Usage:
    PYTHONPATH=src .venv/Scripts/python.exe tests/retrieval_metrics.py
    PYTHONPATH=src .venv/Scripts/python.exe tests/retrieval_metrics.py --tokenizer old
    PYTHONPATH=src .venv/Scripts/python.exe tests/retrieval_metrics.py --dense --json var/metrics.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rag.retriever import BM25Retriever, DenseRetriever, reciprocal_rank_fusion  # noqa: E402
from rag.security import ANONYMOUS                                              # noqa: E402
from rag.store import Store                                                     # noqa: E402

# The tokenizer as it was before 2026-09-14: units glued onto the number, so a query typing
# "71.1%" never matched the document's "71.1 %".  Kept only for the A/B comparison.
OLD_TOKEN_RE = re.compile(
    r"(?:\d+(?:\.\d+)?(?:°c|wt%|mol%|%|mpa|kpa|bar|h|min|s)?)|"
    r"(?:[a-z]+(?:[-_/][a-z0-9]+)*\d*(?:\[[ivx]+\])?)|"
    r"(?:[a-z]?\d+[a-z][a-z0-9]*)|"
    r"(?:[\u4e00-\u9fff])",
    re.IGNORECASE,
)


def load_cases(path: Path) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = []
    for case in raw.get("cases", []):
        relevant = [cid for cid in case.get("relevant_chunk_ids", []) if cid]
        if not relevant:
            print(f"  ! {case['id']}: no relevant_chunk_ids, skipped")
            continue
        cases.append({**case, "relevant": set(relevant)})
    return cases


def ranking_metrics(ranked: list[str], relevant: set[str], ks: list[int]) -> dict:
    first = next((i for i, cid in enumerate(ranked, 1) if cid in relevant), None)
    out: dict[str, float | int | None] = {"mrr": 1.0 / first if first else 0.0, "first_rank": first}
    for k in ks:
        top = ranked[:k]
        hits = sum(1 for cid in top if cid in relevant)
        dcg = sum(1.0 / math.log2(i + 1) for i, cid in enumerate(top, 1) if cid in relevant)
        ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
        out[f"hit@{k}"] = 1.0 if hits else 0.0
        out[f"recall@{k}"] = hits / len(relevant)
        out[f"precision@{k}"] = hits / k
        out[f"ndcg@{k}"] = dcg / ideal if ideal else 0.0
    return out


def mean_metrics(per_case: list[dict], ks: list[int]) -> dict:
    keys = ["mrr", *[f"{m}@{k}" for k in ks for m in ("hit", "recall", "precision", "ndcg")]]
    return {key: sum(row[key] for row in per_case) / len(per_case) for key in keys}


def print_table(title: str, per_case: list[dict], mean: dict, ks: list[int]) -> None:
    heads = "".join(f"{'R@' + str(k):>7}" for k in ks)
    print(f"\n{title}")
    print(f"{'case':<28}{'rank1':>6}{heads}{'P@' + str(ks[-1]):>8}{'MRR':>8}{'nDCG@' + str(ks[-1]):>10}")
    for row in per_case:
        cells = "".join(f"{row['metrics'][f'recall@{k}']:>7.2f}" for k in ks)
        rank = row["metrics"]["first_rank"] or "-"
        print(f"{row['id']:<28}{str(rank):>6}{cells}"
              f"{row['metrics'][f'precision@{ks[-1]}']:>8.2f}"
              f"{row['metrics']['mrr']:>8.3f}"
              f"{row['metrics'][f'ndcg@{ks[-1]}']:>10.3f}")
    cells = "".join(f"{mean[f'recall@{k}']:>7.2f}" for k in ks)
    print(f"{'MEAN':<28}{'':>6}{cells}"
          f"{mean[f'precision@{ks[-1]}']:>8.2f}{mean['mrr']:>8.3f}{mean[f'ndcg@{ks[-1]}']:>10.3f}")


def remote_rows(base_url: str, cases: list[dict], depth: str, ks: list[int],
                timeout: float) -> tuple[list[dict], list[dict]]:
    """Ask a running service for every question and score the ordering it returned.

    /v1/query runs the whole BM25 + dense + RRF chain server side and returns `evidence` in the
    exact order that is handed to the model, so this scores the fused leg - the one that matters -
    without needing the API key locally.  Per-leg BM25/Dense rankings still require the admin
    endpoint /v1/queries/{request_id}.
    """
    rows, meta = [], []
    for case in cases:
        payload = json.dumps({"question": case["question"], "retrieval_depth": depth,
                              "answer_policy": "evidence_first", "top_k": 8}).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + "/v1/query", data=payload,
            headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        info = {"id": case["id"], "elapsed_s": None, "answer_mode": None, "degraded": None,
                "reason": None, "evidence": 0, "http": None, "error": None}
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
                info["http"] = response.status
        except Exception as exc:                                        # network / timeout / 5xx
            info["error"] = f"{type(exc).__name__}: {exc}"
            rows.append({"id": case["id"], "question": case["question"],
                         "gold": sorted(case["relevant"]), "ranked": [],
                         "metrics": ranking_metrics([], case["relevant"], ks)})
            meta.append(info)
            continue
        ranked = [ev["chunk_id"] for ev in body.get("evidence") or []]
        info.update(elapsed_s=round(time.perf_counter() - started, 1),
                    answer_mode=body.get("answer_mode"), degraded=body.get("degraded"),
                    reason=body.get("degradation_reason"), evidence=len(ranked),
                    request_id=body.get("request_id"),
                    timings=body.get("timings_ms"))
        rows.append({"id": case["id"], "question": case["question"],
                     "gold": sorted(case["relevant"]), "ranked": ranked,
                     "metrics": ranking_metrics(ranked, case["relevant"], ks)})
        meta.append(info)
        print(f"  {case['id']:<28} http={info['http']} {info['elapsed_s']:>6}s "
              f"mode={info['answer_mode']} degraded={info['degraded']} "
              f"evidence={info['evidence']}")
    return rows, meta


def legs_from_db(db: Path, request_ids: list[str]) -> dict[str, dict[str, list[str]]]:
    """Read the per-retriever rankings the service persisted for those requests.

    The service writes every candidate to query_candidates(retriever, rank), so a plain SELECT
    yields the exact BM25 / dense / RRF orderings - no admin login and no API key required.
    Returns {request_id: {retriever: [chunk_id, ...]}}.
    """
    import sqlite3
    legs: dict[str, dict[str, list[str]]] = {}
    connection = sqlite3.connect(db)
    try:
        for request_id in request_ids:
            rows = connection.execute(
                "SELECT retriever, chunk_id FROM query_candidates WHERE request_id=? "
                "ORDER BY retriever, rank", (request_id,)).fetchall()
            for retriever, chunk_id in rows:
                legs.setdefault(request_id, {}).setdefault(retriever, []).append(chunk_id)
    finally:
        connection.close()
    return legs


def score_persisted_legs(db: Path, cases: list[dict], request_ids: dict[str, str],
                         ks: list[int]) -> dict:
    """Per-retriever metrics straight out of query_candidates, printed and returned.

    The service persists every candidate with its retriever name and rank, so the BM25 / dense /
    RRF orderings are recoverable with a plain SELECT - no admin login, no API key, and no quota.
    """
    payload: dict = {}
    if not request_ids or not db.exists():
        return payload
    persisted = legs_from_db(db, [rid for rid in request_ids.values() if rid])
    for leg in ("bm25", "dense", "rrf", "rerank", "pre_rerank"):
        leg_rows = []
        for case in cases:
            ranking = persisted.get(request_ids.get(case["id"]) or "", {}).get(leg) or []
            if not ranking:
                continue
            leg_rows.append({"id": case["id"], "question": case["question"],
                             "gold": sorted(case["relevant"]), "ranked": ranking[:max(ks)],
                             "metrics": ranking_metrics(ranking, case["relevant"], ks)})
        if leg_rows:
            leg_mean = mean_metrics([r["metrics"] for r in leg_rows], ks)
            print_table(f"--- {leg} (from query_candidates) ---", leg_rows, leg_mean, ks)
            payload[leg] = {"cases": leg_rows, "mean": leg_mean}
    if not payload:
        print("(no query_candidates rows found for these requests)")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=Path("tests/retrieval_gold.yaml"))
    parser.add_argument("--db", type=Path, default=Path("var/rag.sqlite3"))
    parser.add_argument("--ks", default="1,3,5,8")
    parser.add_argument("--depth", choices=("fast", "standard", "deep"), default="standard",
                        help="which depth budget to score the retrieval legs with")
    parser.add_argument("--tokenizer", choices=("new", "old"), default="new")
    parser.add_argument("--dense", action="store_true", help="also score dense + RRF (needs embedding key)")
    parser.add_argument("--base-url", help="score a RUNNING service instead of a local corpus, "
                                           "e.g. http://127.0.0.1:8000 (no API key needed)")
    parser.add_argument("--timeout", type=float, default=200.0, help="per-question HTTP timeout")
    parser.add_argument("--replay", type=Path,
                        help="re-score the per-leg rankings of a previous --json run; no model calls")
    parser.add_argument("--json", type=Path, help="write the raw numbers here")
    args = parser.parse_args()
    ks = [int(x) for x in args.ks.split(",")]

    from rag.pipeline import Pipeline
    depth = Pipeline.DEPTHS[args.depth]

    cases = load_cases(args.gold)

    if args.replay:
        previous = json.loads(args.replay.read_text(encoding="utf-8"))
        request_ids = {m["id"]: m.get("request_id") for m in previous.get("meta", [])}
        print(f"replaying {len([v for v in request_ids.values() if v])} requests from {args.replay} "
              f"(no model calls)")
        payload = {"transport": "replay", "source": str(args.replay), "ks": ks,
                   "legs": score_persisted_legs(args.db, cases, request_ids, ks)}
    elif args.base_url:
        print(f"gold cases: {len(cases)}   transport: HTTP -> {args.base_url}   depth={args.depth}")
        rows, meta = remote_rows(args.base_url, cases, args.depth, ks, args.timeout)
        mean = mean_metrics([r["metrics"] for r in rows], ks)
        print_table("--- fused (service /v1/query, the list handed to the model) ---", rows, mean, ks)
        generated = [m for m in meta if m["answer_mode"] not in (None, "extractive_demo", "refusal")]
        print(f"\nreal model generation: {len(generated)}/{len(meta)}   "
              f"degraded: {sum(1 for m in meta if m['degraded'])}")
        for m in meta:
            if m["error"]:
                print(f"  ! {m['id']}: {m['error']}")

        # The service persisted every candidate, so the per-leg orderings are recoverable from the
        # database without admin auth.  This is what closes the bm25/dense legs offline.
        request_ids = {m["id"]: m.get("request_id") for m in meta if m.get("request_id")}
        legs_payload = score_persisted_legs(args.db, cases, request_ids, ks)

        payload = {"transport": "http", "base_url": args.base_url, "depth": args.depth,
                   "ks": ks, "cases": rows, "meta": meta, "mean": mean, "legs": legs_payload}
    else:
        chunks = Store(args.db).scoped_chunks(
            ANONYMOUS.tenant_id, ANONYMOUS.subject, collection_ids=[], include_official=True)
        print(f"gold cases: {len(cases)}   corpus: {len(chunks)} chunks / "
              f"{len({c['paper_id'] for c in chunks})} papers   depth={args.depth}")
        print(f"leg budgets: bm25={depth['bm25']} dense={depth['dense']} "
              f"fused={depth['fused']} model-window={depth['evidence']}")
        payload = run_local(args, cases, chunks, depth, ks)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


def run_local(args, cases: list[dict], chunks: list[dict], depth: dict, ks: list[int]) -> dict:

    if args.tokenizer == "old":
        import rag.text
        rag.text.TOKEN_RE = OLD_TOKEN_RE
        print("!! scoring with the PRE-2026-09-14 tokenizer")

    embedder = None
    if args.dense:
        try:
            from rag.config import Settings
            from rag.providers import OpenAICompatibleProvider
            provider = OpenAICompatibleProvider(Settings.load())
            embedder = provider
            print("dense leg: enabled (1 embedding call per question)")
        except Exception as exc:                                   # pragma: no cover - config dependent
            print(f"dense leg: SKIPPED ({exc})")

    bm25 = BM25Retriever(chunks)
    dense = DenseRetriever(chunks) if embedder else None
    deep = max(max(ks), depth["fused"], depth["bm25"], depth["dense"])
    report: dict = {"db": str(args.db), "depth": args.depth, "tokenizer": args.tokenizer,
                    "ks": ks, "legs": {}}

    stages: dict[str, list[dict]] = {}
    for stage in ("bm25", "dense", "fused"):
        stages[stage] = []
    for case in cases:
        lexical = bm25.search(case["question"], depth["bm25"])
        dense_hits = []
        if dense is not None:
            vector = embedder.embed([case["question"]])[0]
            dense_hits = dense.search(vector, depth["dense"])
        fused = reciprocal_rank_fusion(lexical, dense_hits, top_k=depth["fused"]) if dense_hits else []

        for stage, fetched in (("bm25", lexical), ("dense", dense_hits), ("fused", fused)):
            if not fetched:
                continue
            ranked = [ev.chunk_id for ev in fetched][:deep]
            stages[stage].append({"id": case["id"], "question": case["question"],
                                  "gold": sorted(case["relevant"]),
                                  "ranked": ranked[:max(ks)],
                                  "metrics": ranking_metrics(ranked, case["relevant"], ks)})
        shown = [ev.chunk_id for ev in (fused or lexical)][:depth["evidence"]]
        report.setdefault("model_window", []).append(
            {"id": case["id"], "window_size": depth["evidence"],
             "hit": any(cid in case["relevant"] for cid in shown),
             "chunk_ids": shown})

    for stage, rows in stages.items():
        if not rows:
            continue
        mean = mean_metrics([r["metrics"] for r in rows], ks)
        print_table(f"--- {stage} ---", rows, mean, ks)
        report["legs"][stage] = {"cases": rows, "mean": mean}

    window_hits = sum(1 for w in report["model_window"] if w["hit"])
    print(f"\nmodel window ({depth['evidence']} chunks) contains a gold chunk in "
          f"{window_hits}/{len(report['model_window'])} cases")
    return report


if __name__ == "__main__":
    raise SystemExit(main())
