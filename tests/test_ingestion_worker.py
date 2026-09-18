from types import SimpleNamespace

from rag.tasks import IngestionWorker


def test_worker_marks_unexpected_failure_and_runs_next_job():
    jobs = iter([
        {"run_id": "run_first", "request_id": "req_first", "file_path": None, "force": 0},
        {"run_id": "run_second", "request_id": "req_second", "file_path": None, "force": 0},
    ])

    class Store:
        failed = []

        def claim_ingestion_job(self):
            try:
                return next(jobs)
            except StopIteration:
                worker._stop.set()
                return None

        def update_run(self, *args, **kwargs):
            self.failed.append((args, kwargs))

        def update_run_document(self, *args, **kwargs):
            self.failed.append((args, kwargs))

    class Log:
        def emit(self, *args, **kwargs):
            pass

    class Pipeline:
        settings = SimpleNamespace(debug=False)
        store = Store()
        log = Log()

        def cleanup_expired_documents(self):
            pass

        def ingest(self, **kwargs):
            if kwargs["run_id"] == "run_first":
                raise RuntimeError("unexpected worker failure")
            seen.append(kwargs["run_id"])

    seen = []
    worker = IngestionWorker(Pipeline())
    worker._loop()
    assert seen == ["run_second"]
    assert any(args[0] == "run_first" and kwargs["status"] == "failed"
               for args, kwargs in worker.pipeline.store.failed)
