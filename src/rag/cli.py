from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from .errors import error_info
from .pipeline import Pipeline


def output(value: object) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Li_Jia research-paper RAG")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check runtime dependencies")
    ingest = commands.add_parser("ingest", help="ingest PDFs")
    ingest.add_argument("--force", action="store_true")
    ingest.add_argument("--file", type=Path)
    query = commands.add_parser("query", help="retrieve and answer")
    query.add_argument("question")
    query.add_argument("--top-k", type=int, default=8)
    query.add_argument("--paper-id", action="append", dest="paper_ids")
    status = commands.add_parser("status", help="show run state")
    status.add_argument("--run-id")
    serve = commands.add_parser("serve", help="start HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("hash-password", help="interactively create an admin password hash")
    evaluate = commands.add_parser("evaluate", help="run the small repeatable diagnostic question set")
    evaluate.add_argument("--cases", type=Path, default=Path("tests/eval_cases.yaml"))
    commands.add_parser("embed", help="atomically rebuild vectors without reparsing PDFs")
    backup = commands.add_parser("backup", help="create a consistent non-overwriting SQLite backup")
    backup.add_argument("--output", type=Path)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "hash-password":
            from .security import hash_password
            first = getpass.getpass("Admin password: ")
            second = getpass.getpass("Repeat password: ")
            if not first or first != second:
                raise ValueError("passwords do not match or are empty")
            output({"password_hash": hash_password(first)})
            return 0
        pipeline = Pipeline()
        if args.command == "doctor":
            output(pipeline.doctor())
        elif args.command == "ingest":
            result = pipeline.ingest(file=args.file, force=args.force)
            output(result)
            return 0 if result["status"] in {"completed", "partial_failed"} else 1
        elif args.command == "query":
            output(pipeline.query(args.question, top_k=args.top_k,
                                  paper_ids=args.paper_ids).to_dict())
        elif args.command == "status":
            output(pipeline.store.run(args.run_id) if args.run_id else pipeline.store.latest_runs())
        elif args.command == "serve":
            import uvicorn
            uvicorn.run("rag.api:app", host=args.host, port=args.port)
        elif args.command == "evaluate":
            from .evaluation import run_cases
            result = run_cases(pipeline, args.cases.resolve())
            output(result)
            return 0 if result["failed"] == 0 else 1
        elif args.command == "embed":
            result = pipeline.rebuild_embeddings()
            output(result)
            return 0 if result["status"] == "completed" else 1
        elif args.command == "backup":
            output(pipeline.store.backup(args.output))
        return 0
    except Exception as exc:
        output({"status": "failed", "error": error_info(exc, getattr(exc, "stage", "cli")).to_dict()})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
