from __future__ import annotations

from pathlib import Path
import hashlib
import hmac
import time
import sqlite3

from fastapi.testclient import TestClient
import fitz

from rag.config import Settings
from rag.pipeline import Pipeline
from rag.security import SessionSecurity
from rag.conversation import ContextualQueryRewriter
from rag.store import MIGRATIONS, SCHEMA, Store
from rag.errors import RagError


def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    var = tmp_path / "var"
    return Settings(
        project_dir=tmp_path, data_dir=data, var_dir=var, db_path=var / "rag.sqlite3",
        log_path=var / "logs" / "rag.jsonl", debug=False, ocr_enabled=False,
        min_page_chars=20, chunk_tokens=80, chunk_overlap=10, chat_provider="extractive",
        chat_base_url=None, chat_api_key=None, chat_model=None, chat_timeout_seconds=5,
    )


def make_pdf(path: Path, text: str = "Xylose produced furfural with 78 percent yield at 180 C.") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def test_contextual_rewriter_is_bounded_and_preserves_standalone_questions():
    rewriter = ContextualQueryRewriter(max_messages=2)
    history = [
        {"role": "user", "content": "很早的问题"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "木糖制糠醛使用了哪些催化剂？"},
    ]
    rewritten = rewriter.rewrite("这些条件下的收率如何？", history)
    assert "木糖制糠醛" in rewritten and "后续问题" in rewritten
    assert rewriter.rewrite("糠醛的最高收率是多少？", history) == "糠醛的最高收率是多少？"


def test_multiscope_migrations_are_idempotent_and_create_official_scope(tmp_path: Path):
    # A fresh store models the same migration runner used for legacy databases.
    pipeline = Pipeline(settings(tmp_path))
    pipeline.store.__class__(pipeline.settings.db_path)
    with pipeline.store.connect() as conn:
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 14
        row = conn.execute("SELECT * FROM collections WHERE collection_id='col_official_default'").fetchone()
        assert row and row["scope_type"] == "official" and row["owner_id"] is None


def test_realistic_v10_database_upgrades_without_changing_existing_ids(tmp_path: Path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        for version, sql in MIGRATIONS:
            if version > 10:
                break
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_migrations VALUES(?,CURRENT_TIMESTAMP)", (version,))
        conn.execute(
            """INSERT INTO papers(paper_id,source_hash,file_name,file_path,size_bytes,status,
               parser_version) VALUES(?,?,?,?,?,'ready','legacy-parser')""",
            ("paper_0123456789abcdef01234567", "a" * 64, "legacy.pdf", str(tmp_path / "legacy.pdf"), 99),
        )
    Store(path)
    Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 14
        mapped = conn.execute("SELECT paper_id,scope_type FROM collection_documents").fetchone()
        assert mapped == ("paper_0123456789abcdef01234567", "official")


def test_user_resources_are_server_scoped_and_cross_user_hidden(tmp_path: Path, monkeypatch):
    import rag.api as api

    local = Pipeline(settings(tmp_path))
    monkeypatch.setenv("RAG_DEV_AUTH_ENABLED", "1")
    monkeypatch.setenv("RAG_ENV", "development")
    monkeypatch.setattr(api, "pipeline", local)
    monkeypatch.setattr(api, "security", SessionSecurity())
    headers_a = {"X-RAG-Dev-Subject": "alice"}
    headers_b = {"X-RAG-Dev-Subject": "bob"}
    with TestClient(api.app) as client:
        assert client.get("/v1/collections", headers={"X-User-ID": "alice"}).status_code == 401
        created = client.post("/v1/collections", headers=headers_a, json={"name": "催化研究"})
        assert created.status_code == 201
        collection_id = created.json()["collection_id"]
        alice = client.get("/v1/collections", headers=headers_a).json()["items"]
        bob = client.get("/v1/collections", headers=headers_b).json()["items"]
        assert collection_id in {item["collection_id"] for item in alice}
        assert collection_id not in {item["collection_id"] for item in bob}
        assert client.patch(f"/v1/collections/{collection_id}", headers=headers_b,
                            json={"name": "越权"}).status_code == 404

        conversation = client.post("/v1/conversations", headers=headers_a,
                                   json={"title": "木糖实验"}).json()
        conversation_id = conversation["conversation_id"]
        assert client.get(f"/v1/conversations/{conversation_id}", headers=headers_b).status_code == 404
        restored = client.get(f"/v1/conversations/{conversation_id}", headers=headers_a)
        assert restored.status_code == 200 and restored.json()["title"] == "木糖实验"


def test_query_warns_when_selected_document_is_not_ready(tmp_path: Path):
    pipeline = Pipeline(settings(tmp_path))
    collection = pipeline.store.create_private_collection("default", "alice", "处理中库")
    pipeline.store.create_ingestion_job(
        "run_pending", "req_pending", file_path=str(tmp_path / "pending.pdf"), force=False,
        tenant_id="default", owner_id="alice", collection_id=collection["collection_id"],
        scope_type="private", document_id="doc_pending",
    )
    result = pipeline.query(
        "xylose furfural", tenant_id="default", subject="alice",
        collection_ids=[collection["collection_id"]], include_official=False,
    )
    assert result.insufficient_evidence and result.corpus_incomplete
    assert any(item["code"] == "DOCUMENTS_NOT_READY" for item in result.warnings)


def test_dev_identity_is_disabled_in_production(tmp_path: Path, monkeypatch):
    import rag.api as api

    monkeypatch.setattr(api, "pipeline", Pipeline(settings(tmp_path)))
    monkeypatch.setenv("RAG_DEV_AUTH_ENABLED", "1")
    monkeypatch.setenv("RAG_ENV", "production")
    with TestClient(api.app) as client:
        assert client.get("/v1/conversations", headers={"X-RAG-Dev-Subject": "alice"}).status_code == 401


def test_trusted_proxy_identity_requires_fresh_hmac_signature(tmp_path: Path, monkeypatch):
    import rag.api as api

    monkeypatch.setattr(api, "pipeline", Pipeline(settings(tmp_path)))
    monkeypatch.setattr(api, "security", SessionSecurity())
    monkeypatch.setenv("RAG_TRUSTED_IDENTITY_SECRET", "integration-shared-secret")
    timestamp = str(int(time.time()))
    canonical = "\n".join(("tenant-a", "alice", "user", timestamp)).encode()
    signature = hmac.new(b"integration-shared-secret", canonical, hashlib.sha256).hexdigest()
    headers = {
        "X-RAG-Tenant": "tenant-a", "X-RAG-Subject": "alice", "X-RAG-Roles": "user",
        "X-RAG-Identity-Timestamp": timestamp, "X-RAG-Identity-Signature": signature,
    }
    with TestClient(api.app) as client:
        created = client.post("/v1/conversations", headers=headers, json={"title": "可信身份"})
        assert created.status_code == 201 and created.json()["tenant_id"] == "tenant-a"
        forged = {**headers, "X-RAG-Identity-Signature": "0" * 64}
        assert client.get("/v1/conversations", headers=forged).status_code == 401


def test_private_upload_is_async_scoped_and_retrievable_only_by_owner(tmp_path: Path, monkeypatch):
    import time
    import rag.api as api

    local = Pipeline(settings(tmp_path))
    monkeypatch.setattr(api, "pipeline", local)
    monkeypatch.setattr(api, "security", SessionSecurity())
    monkeypatch.setenv("RAG_DEV_AUTH_ENABLED", "1")
    monkeypatch.setenv("RAG_ENV", "development")
    headers = {"X-RAG-Dev-Subject": "alice"}
    pdf = tmp_path / "private.pdf"
    make_pdf(pdf)
    with TestClient(api.app) as client:
        collection = client.post("/v1/collections", headers=headers,
                                 json={"name": "私人催化库"}).json()
        created = client.post(
            f"/v1/collections/{collection['collection_id']}/documents",
            headers={**headers, "X-Filename": "private.pdf", "Content-Type": "application/pdf"},
            content=pdf.read_bytes(),
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        document_id = created.json()["document_id"]
        status = client.get(f"/v1/documents/{document_id}/status", headers=headers)
        assert status.status_code == 200 and status.json()["status"] in {"queued", "running", "completed"}
        assert client.get(f"/v1/documents/{document_id}/status", headers={
            "X-RAG-Dev-Subject": "bob"
        }).status_code == 404
        for _ in range(100):
            run = local.store.run(run_id)
            if run and run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert run and run["status"] == "completed"
        documents = client.get(
            f"/v1/collections/{collection['collection_id']}/documents", headers=headers
        ).json()["items"]
        assert len(documents) == 1 and documents[0]["source_name"] == "private.pdf"
        assert client.get("/v1/papers").json()["count"] == 0
        assert client.get(f"/v1/papers/{documents[0]['paper_id']}").status_code == 404
        assert client.get(
            f"/v1/collections/{collection['collection_id']}/documents",
            headers={"X-RAG-Dev-Subject": "bob"},
        ).status_code == 404
        answer = client.post("/v1/query", headers=headers, json={
            "question": "xylose furfural yield", "include_official": False,
            "collection_ids": [collection["collection_id"]],
        })
        assert answer.status_code == 200
        evidence = answer.json()["evidence"]
        assert evidence and evidence[0]["source_type"] == "private"
        snapshot = local.store.query_diagnostic(answer.json()["request_id"])
        assert snapshot["tenant_id"] == "default" and snapshot["subject"] == "alice"
        assert snapshot["collection_ids"] == [collection["collection_id"]]
        assert snapshot["include_official"] == 0
        assert any(item["scope_type"] == "private" and
                   item["collection_id"] == collection["collection_id"]
                   for item in snapshot["candidates"] if item["selected"])
        retrieved = client.post("/v1/retrieve", headers=headers, json={
            "question": "xylose furfural yield", "include_official": False,
            "collection_ids": [collection["collection_id"]],
        })
        assert retrieved.status_code == 200 and retrieved.json()["results"]
        evidence_id = answer.json()["evidence"][0]["evidence_id"]
        detail = client.get(f"/v1/evidence/{evidence_id}", headers=headers)
        assert detail.status_code == 200 and detail.json()["scope_type"] == "private"
        assert client.get(f"/v1/evidence/{evidence_id}", headers={
            "X-RAG-Dev-Subject": "bob"
        }).status_code == 404
        hidden = client.post("/v1/query", headers={"X-RAG-Dev-Subject": "bob"}, json={
            "question": "xylose furfural yield", "include_official": False,
            "collection_ids": [collection["collection_id"]],
        }).json()
        assert hidden["insufficient_evidence"] and not hidden["evidence"]
        strict = client.post("/v1/query", headers=headers, json={
            "question": "xylose furfural yield", "include_official": False,
            "collection_ids": [collection["collection_id"]], "paper_ids": ["paper_not_allowed"],
        }).json()
        assert strict["insufficient_evidence"] and not strict["evidence"]

        bob_headers = {"X-RAG-Dev-Subject": "bob"}
        bob_collection = client.post("/v1/collections", headers=bob_headers,
                                     json={"name": "Bob 的独立库"}).json()
        bob_upload = client.post(
            f"/v1/collections/{bob_collection['collection_id']}/documents",
            headers={**bob_headers, "X-Filename": "same-content.pdf",
                     "Content-Type": "application/pdf"}, content=pdf.read_bytes(),
        ).json()
        for _ in range(100):
            bob_run = local.store.run(bob_upload["run_id"])
            if bob_run and bob_run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert bob_run and bob_run["status"] == "completed"
        bob_documents = client.get(
            f"/v1/collections/{bob_collection['collection_id']}/documents", headers=bob_headers
        ).json()["items"]
        assert len(bob_documents) == 1
        assert client.delete(f"/v1/documents/{document_id}", headers=headers).status_code == 204
        bob_answer = client.post("/v1/query", headers=bob_headers, json={
            "question": "xylose furfural yield", "include_official": False,
            "collection_ids": [bob_collection["collection_id"]],
        }).json()
        assert bob_answer["evidence"] and bob_answer["evidence"][0]["source_type"] == "private"


def test_temporary_document_is_conversation_scoped_promotable_and_cleaned(tmp_path: Path, monkeypatch):
    import time
    import rag.api as api

    local = Pipeline(settings(tmp_path))
    monkeypatch.setattr(api, "pipeline", local)
    monkeypatch.setattr(api, "security", SessionSecurity())
    monkeypatch.setenv("RAG_DEV_AUTH_ENABLED", "1")
    monkeypatch.setenv("RAG_ENV", "development")
    headers = {"X-RAG-Dev-Subject": "alice"}
    pdf = tmp_path / "temporary.pdf"
    make_pdf(pdf, "Temporary zeolite evidence reports xylose furfural yield 66 percent.")
    with TestClient(api.app) as client:
        first = client.post("/v1/conversations", headers=headers, json={"title": "会话一"}).json()
        second = client.post("/v1/conversations", headers=headers, json={"title": "会话二"}).json()
        private = client.post("/v1/collections", headers=headers, json={"name": "长期库"}).json()
        created = client.post(
            f"/v1/conversations/{first['conversation_id']}/documents",
            headers={**headers, "X-Filename": "temporary.pdf", "Content-Type": "application/pdf"},
            content=pdf.read_bytes(),
        )
        assert created.status_code == 202 and created.json()["expires_at"]
        run_id, document_id = created.json()["run_id"], created.json()["document_id"]
        for _ in range(100):
            run = local.store.run(run_id)
            if run and run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.02)
        assert run and run["status"] == "completed"
        own = client.post("/v1/query", headers=headers, json={
            "question": "temporary zeolite xylose yield", "include_official": False,
            "conversation_id": first["conversation_id"],
        }).json()
        assert own["evidence"] and own["evidence"][0]["source_type"] == "temporary"
        restored = client.get(f"/v1/conversations/{first['conversation_id']}", headers=headers).json()
        assert [item["role"] for item in restored["messages"]] == ["user", "assistant"]
        assert restored["messages"][0]["original_question"] == "temporary zeolite xylose yield"
        follow_up = client.post("/v1/query", headers=headers, json={
            "question": "这些结果的收率如何？", "include_official": False,
            "conversation_id": first["conversation_id"],
        })
        assert follow_up.status_code == 200
        continued = client.get(f"/v1/conversations/{first['conversation_id']}", headers=headers).json()
        assert len(continued["messages"]) == 4
        assert "后续问题" in continued["messages"][2]["retrieval_question"]
        other = client.post("/v1/query", headers=headers, json={
            "question": "temporary zeolite xylose yield", "include_official": False,
            "conversation_id": second["conversation_id"],
        }).json()
        assert not other["evidence"]

        promoted = client.post(f"/v1/documents/{document_id}/promote", headers=headers,
                               json={"collection_id": private["collection_id"]})
        assert promoted.status_code == 201
        promoted_id = promoted.json()["document_id"]
        promoted_path = Path(local.store.document_for_user(
            promoted_id, "default", "alice"
        )["stored_path"])
        assert promoted_path.is_file()
        combined = client.post("/v1/query", headers=headers, json={
            "question": "temporary zeolite xylose yield", "include_official": False,
            "collection_ids": [private["collection_id"]],
            "conversation_id": first["conversation_id"],
        }).json()
        combined_evidence = combined["evidence"][0]
        combined_detail = client.get(
            f"/v1/evidence/{combined_evidence['evidence_id']}",
            headers=headers,
            params={"conversation_id": first["conversation_id"]},
        ).json()
        assert combined_detail["document_id"] == combined_evidence["document_id"]
        assert combined_detail["scope_type"] == combined_evidence["source_type"]

        with local.store.connect() as conn:
            conn.execute("UPDATE collection_documents SET expires_at=? WHERE document_id=?",
                         ("2000-01-01T00:00:00+00:00", document_id))
        cleanup = local.cleanup_expired_documents()
        assert cleanup["deleted"] == 1
        assert local.store.document_for_user(document_id, "default", "alice") is None
        assert local.store.document_for_user(promoted_id, "default", "alice") is not None
        assert promoted_path.is_file()
        assert local.store.chunks([promoted.json()["paper_id"]])


def test_three_scopes_combine_in_sql_and_unshared_temp_cleanup_removes_physical_rows(tmp_path: Path):
    pipeline = Pipeline(settings(tmp_path))
    official_pdf = tmp_path / "official.pdf"
    private_pdf = tmp_path / "private.pdf"
    temporary_pdf = pipeline.settings.var_dir / "temporary" / "default" / "alice" / "doc_temp_scope" / "temporary.pdf"
    temporary_pdf.parent.mkdir(parents=True)
    make_pdf(official_pdf, "Official marker alpha cellulose evidence.")
    make_pdf(private_pdf, "Private marker beta lignin evidence.")
    make_pdf(temporary_pdf, "Temporary marker gamma xylose evidence.")
    assert pipeline.ingest(file=official_pdf)["status"] == "completed"
    private = pipeline.store.create_private_collection("default", "alice", "私人库")
    conversation = pipeline.store.create_conversation("default", "alice", "组合范围")
    temp_collection = next(item for item in pipeline.store.accessible_collections("default", "alice")
                           if item["scope_type"] == "temporary")
    private_scope = {
        "document_id": "doc_private_scope", "collection_id": private["collection_id"],
        "tenant_id": "default", "owner_id": "alice", "scope_type": "private",
        "conversation_id": None, "expires_at": None,
    }
    temp_scope = {
        "document_id": "doc_temp_scope", "collection_id": temp_collection["collection_id"],
        "tenant_id": "default", "owner_id": "alice", "scope_type": "temporary",
        "conversation_id": conversation["conversation_id"],
        "expires_at": "2999-01-01T00:00:00+00:00",
    }
    assert pipeline.ingest(file=private_pdf, document_scope=private_scope)["status"] == "completed"
    assert pipeline.ingest(file=temporary_pdf, document_scope=temp_scope)["status"] == "completed"
    chunks = pipeline.store.scoped_chunks(
        "default", "alice", collection_ids=[private["collection_id"]],
        conversation_id=conversation["conversation_id"], include_official=True,
    )
    assert {item["scope_type"] for item in chunks} == {"official", "private", "temporary"}
    unselected = pipeline.store.scoped_chunks(
        "default", "alice", collection_ids=[], conversation_id=None, include_official=True,
    )
    assert {item["scope_type"] for item in unselected} == {"official"}

    temp_document = pipeline.store.document_for_user("doc_temp_scope", "default", "alice")
    temp_paper_id = temp_document["paper_id"]
    with pipeline.store.connect() as conn:
        conn.execute("UPDATE collection_documents SET expires_at=? WHERE document_id=?",
                     ("2000-01-01T00:00:00+00:00", "doc_temp_scope"))
    cleaned = pipeline.cleanup_expired_documents()
    assert cleaned["deleted"] == 1 and not temporary_pdf.exists()
    assert pipeline.store.paper(temp_paper_id) is None
    assert not pipeline.store.pages(temp_paper_id)
    assert not pipeline.store.chunks([temp_paper_id])


def test_document_delete_refuses_paths_outside_managed_roots(tmp_path: Path):
    pipeline = Pipeline(settings(tmp_path))
    pdf = tmp_path / "outside-managed-root.pdf"
    make_pdf(pdf)
    collection = pipeline.store.create_private_collection("default", "alice", "安全路径")
    scope = {
        "document_id": "doc_outside", "collection_id": collection["collection_id"],
        "tenant_id": "default", "owner_id": "alice", "scope_type": "private",
        "conversation_id": None, "expires_at": None,
    }
    assert pipeline.ingest(file=pdf, document_scope=scope)["status"] == "completed"
    try:
        pipeline.delete_scoped_document("doc_outside", "default", "alice")
    except RagError as exc:
        assert exc.code == "DOCUMENT_PATH_UNSAFE"
    else:
        raise AssertionError("unsafe deletion path must be rejected")
    assert pdf.is_file()
    assert pipeline.store.document_for_user("doc_outside", "default", "alice") is not None
