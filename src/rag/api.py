from __future__ import annotations

import re
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError

from .errors import RagError, error_info
from .extraction import EXTRACTION_JSON_SCHEMA
from .pipeline import Pipeline
from .conversation import ContextualQueryRewriter
from .security import Principal, SessionSecurity, principal_from_request, require_user
from .tasks import IngestionWorker

pipeline = Pipeline()
security = SessionSecurity()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.security = security
    worker = IngestionWorker(pipeline)
    app.state.worker = worker
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Li_Jia Research RAG", version="0.2.0", lifespan=lifespan)
web_dir = Path(__file__).with_name("web")
app.mount("/assets", StaticFiles(directory=web_dir), name="assets")


def error_payload(request: Request, code: str, stage: str, message: str,
                  exception_type: str, *, retryable: bool = False) -> dict:
    return {"error": {"error_code": code, "stage": stage, "message": message,
                      "exception_type": exception_type, "retryable": retryable,
                      "request_id": getattr(request.state, "request_id", None)}}


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied = request.headers.get("x-request-id", "")
    request.state.request_id = supplied if re.fullmatch(r"req_[a-f0-9]{32}", supplied) else f"req_{uuid.uuid4().hex}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class IngestionRequest(BaseModel):
    force: bool = False


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=8, ge=1, le=50)
    paper_ids: list[str] | None = Field(default=None, max_length=100)
    collection_ids: list[str] | None = Field(default=None, max_length=50)
    conversation_id: str | None = Field(default=None, max_length=80)
    include_official: bool = True


class NamedResourceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ConversationRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=120)


class PromoteRequest(BaseModel):
    collection_id: str = Field(min_length=1, max_length=80)


def current_user(request: Request) -> Principal:
    return require_user(principal_from_request(request))


def require_admin(request: Request) -> None:
    if not security.configured:
        raise HTTPException(status_code=503, detail={"code": "AUTH_NOT_CONFIGURED", "message": "管理员密码尚未配置"})
    if not security.valid(request.cookies.get(security.cookie_name)):
        raise HTTPException(status_code=401, detail={"code": "AUTH_REQUIRED", "message": "需要管理员登录"})


def checked_paper(paper_id: str) -> dict:
    if not re.fullmatch(r"paper_[a-f0-9]{24}", paper_id):
        raise HTTPException(status_code=400, detail="invalid paper_id")
    item = pipeline.store.paper(paper_id)
    if not item:
        raise HTTPException(status_code=404, detail="paper not found")
    return item


def public_paper(item: dict) -> dict:
    safe = {key: value for key, value in item.items() if key not in {"file_path", "source_hash"}}
    safe["attachments"] = [
        {key: value for key, value in attachment.items() if key != "file_path"}
        for attachment in safe.get("attachments", [])
    ]
    if safe.get("last_error"):
        safe["last_error"] = {key: safe["last_error"].get(key) for key in
                              ("error_code", "stage", "message", "retryable")}
    safe["chunk_count"] = len(pipeline.store.paper_chunks(item["paper_id"], limit=500))
    return safe


async def validated_pdf_body(request: Request) -> tuple[str, bytes, str]:
    raw_name = unquote(request.headers.get("x-filename", "upload.pdf"))
    name = Path(raw_name).name
    if name != raw_name or not name.lower().endswith(".pdf") or len(name) > 180:
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_FILENAME_INVALID"})
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/pdf":
        raise HTTPException(status_code=415, detail={"code": "UPLOAD_MIME_INVALID", "message": "仅接受 application/pdf"})
    maximum = 50 * 1024 * 1024
    if int(request.headers.get("content-length", "0") or 0) > maximum:
        raise HTTPException(status_code=413, detail={"code": "UPLOAD_TOO_LARGE"})
    content = bytearray()
    async for block in request.stream():
        content.extend(block)
        if len(content) > maximum:
            raise HTTPException(status_code=413, detail={"code": "UPLOAD_TOO_LARGE"})
    if len(content) < 5 or not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_NOT_PDF"})
    try:
        import fitz
        with fitz.open(stream=bytes(content), filetype="pdf") as document:
            if document.needs_pass:
                raise HTTPException(status_code=400, detail={"code": "UPLOAD_PDF_ENCRYPTED"})
            if document.page_count < 1:
                raise HTTPException(status_code=400, detail={"code": "UPLOAD_PDF_EMPTY"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_PDF_INVALID"}) from exc
    data = bytes(content)
    return name, data, hashlib.sha256(data).hexdigest()


async def queue_scoped_upload(request: Request, principal: Principal, *, collection: dict,
                              conversation_id: str | None = None) -> dict:
    name, content, source_hash = await validated_pdf_body(request)
    active = pipeline.store.active_ingestion_for_hash(source_hash, collection["collection_id"])
    if active:
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TASK_ACTIVE", **active})
    document_id = f"doc_{uuid.uuid4().hex}"
    run_id = f"run_{uuid.uuid4().hex}"
    expires_at = None
    root_name = "temporary" if collection["scope_type"] == "temporary" else "private"
    if root_name == "temporary":
        expires_at = (datetime.now(timezone.utc) + timedelta(
            hours=pipeline.settings.temp_document_ttl_hours)).isoformat()
    destination = (pipeline.settings.var_dir / root_name / principal.tenant_id /
                   principal.subject / document_id / name).resolve()
    expected_root = (pipeline.settings.var_dir / root_name).resolve()
    if not destination.is_relative_to(expected_root):
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_PATH_INVALID"})
    destination.parent.mkdir(parents=True, exist_ok=False)
    destination.write_bytes(content)
    pipeline.store.create_ingestion_job(
        run_id, request.state.request_id, file_path=str(destination), force=False,
        source_hash=source_hash, tenant_id=principal.tenant_id, owner_id=principal.subject,
        collection_id=collection["collection_id"], scope_type=collection["scope_type"],
        conversation_id=conversation_id, expires_at=expires_at, document_id=document_id,
    )
    request.app.state.worker.notify()
    return {"document_id": document_id, "run_id": run_id,
            "request_id": request.state.request_id, "status": "queued", "expires_at": expires_at}


@app.exception_handler(RagError)
async def rag_error_handler(request: Request, exc: RagError):
    info = error_info(exc, exc.stage).to_dict()
    info["request_id"] = request.state.request_id
    return JSONResponse(status_code=400, content={"error": info})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=error_payload(
        request, "REQUEST_VALIDATION_FAILED", "api", str(exc)[:1000], "RequestValidationError"))


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    code = detail.get("code", "HTTP_ERROR") if isinstance(detail, dict) else "HTTP_ERROR"
    message = detail.get("message", code) if isinstance(detail, dict) else str(detail)
    return JSONResponse(status_code=exc.status_code, content=error_payload(
        request, code, "api", message, "HTTPException", retryable=exc.status_code >= 500))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    pipeline.log.emit("api_failed", request_id=request.state.request_id,
                      error_code="INTERNAL_ERROR", exception_type=type(exc).__name__)
    return JSONResponse(status_code=500, content=error_payload(
        request, "INTERNAL_ERROR", "api", "服务器内部错误，请使用 request_id 查询日志",
        type(exc).__name__, retryable=True))


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(web_dir / "index.html")


@app.get("/health")
def health() -> dict:
    database_ok = pipeline.store.health()
    return {"status": "ok" if database_ok else "failed",
            "database": "ok" if database_ok else "failed"}


@app.get("/ready")
def ready() -> dict:
    report = pipeline.doctor()
    value = report["database_ok"] and report["data_dir_exists"] and report["dependencies"]["pymupdf"]
    if report["ocr_enabled"]:
        value = value and report["dependencies"]["rapidocr"]
    degraded_components = []
    if not report["chat_configured"]:
        degraded_components.append("chat")
    if not report["embedding_configured"]:
        degraded_components.append("embedding")
    if not security.configured:
        degraded_components.append("auth")
    public_report = {key: value for key, value in report.items() if key not in {"data_dir", "database"}}
    return {"ready": value and security.configured, "core_ready": value,
            "auth_configured": security.configured,
            "degraded_components": degraded_components, **public_report}


@app.post("/v1/auth/login")
def login(body: LoginRequest, response: Response) -> dict:
    token = security.login(body.password)
    if not token:
        raise HTTPException(status_code=401, detail={"code": "LOGIN_FAILED", "message": "密码错误或管理员未配置"})
    response.set_cookie(security.cookie_name, token, httponly=True, samesite="strict",
                        secure=security.cookie_secure, max_age=security.ttl_seconds)
    return {"authenticated": True}


@app.post("/v1/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(security.cookie_name)
    return {"authenticated": False}


@app.get("/v1/auth/status")
def auth_status(request: Request) -> dict:
    principal = principal_from_request(request)
    return {"configured": security.configured,
            "authenticated": principal.subject != "anonymous",
            "subject": None if principal.subject == "anonymous" else principal.subject,
            "roles": sorted(principal.roles), "auth_method": principal.auth_method}


@app.get("/v1/collections")
def list_collections(principal: Principal = Depends(current_user)) -> dict:
    items = pipeline.store.accessible_collections(principal.tenant_id, principal.subject)
    return {"count": len(items), "items": items}


@app.post("/v1/collections", status_code=201)
def create_collection(body: NamedResourceRequest,
                      principal: Principal = Depends(current_user)) -> dict:
    return pipeline.store.create_private_collection(
        principal.tenant_id, principal.subject, body.name.strip()
    )


@app.patch("/v1/collections/{collection_id}")
def rename_collection(collection_id: str, body: NamedResourceRequest,
                      principal: Principal = Depends(current_user)) -> dict:
    if not pipeline.store.rename_private_collection(
            collection_id, principal.tenant_id, principal.subject, body.name.strip()):
        raise HTTPException(status_code=404, detail={"code": "COLLECTION_NOT_FOUND"})
    return pipeline.store.collection_for_owner(collection_id, principal.tenant_id, principal.subject) or {}


@app.delete("/v1/collections/{collection_id}", status_code=204)
def delete_collection(collection_id: str, principal: Principal = Depends(current_user)) -> Response:
    if not pipeline.delete_private_collection(collection_id, principal.tenant_id, principal.subject):
        raise HTTPException(status_code=404, detail={"code": "COLLECTION_NOT_FOUND"})
    return Response(status_code=204)


@app.get("/v1/collections/{collection_id}/documents")
def list_collection_documents(collection_id: str, q: str | None = None,
                              principal: Principal = Depends(current_user)) -> dict:
    items = pipeline.store.collection_documents(
        collection_id, principal.tenant_id, principal.subject, query=q
    )
    if items is None:
        raise HTTPException(status_code=404, detail={"code": "COLLECTION_NOT_FOUND"})
    return {"count": len(items), "items": items}


@app.post("/v1/collections/{collection_id}/documents", status_code=202)
async def upload_collection_document(collection_id: str, request: Request,
                                     principal: Principal = Depends(current_user)) -> dict:
    collection = pipeline.store.collection_for_owner(
        collection_id, principal.tenant_id, principal.subject
    )
    if not collection or collection["scope_type"] != "private":
        raise HTTPException(status_code=404, detail={"code": "COLLECTION_NOT_FOUND"})
    return await queue_scoped_upload(request, principal, collection=collection)


@app.post("/v1/conversations", status_code=201)
def create_conversation(body: ConversationRequest,
                        principal: Principal = Depends(current_user)) -> dict:
    return pipeline.store.create_conversation(
        principal.tenant_id, principal.subject, body.title.strip()
    )


@app.post("/v1/conversations/{conversation_id}/documents", status_code=202)
async def upload_temporary_document(conversation_id: str, request: Request,
                                    principal: Principal = Depends(current_user)) -> dict:
    conversation = pipeline.store.conversation(
        conversation_id, principal.tenant_id, principal.subject
    )
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
    collections = pipeline.store.accessible_collections(principal.tenant_id, principal.subject)
    collection = next((item for item in collections if item["scope_type"] == "temporary" and
                       item["conversation_id"] == conversation_id), None)
    if not collection:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
    return await queue_scoped_upload(
        request, principal, collection=collection, conversation_id=conversation_id
    )


@app.get("/v1/conversations/{conversation_id}/documents")
def list_temporary_documents(conversation_id: str,
                             principal: Principal = Depends(current_user)) -> dict:
    conversation = pipeline.store.conversation(conversation_id, principal.tenant_id, principal.subject)
    if not conversation:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
    collection = next((item for item in pipeline.store.accessible_collections(
        principal.tenant_id, principal.subject
    ) if item["scope_type"] == "temporary" and item["conversation_id"] == conversation_id), None)
    items = pipeline.store.collection_documents(
        collection["collection_id"], principal.tenant_id, principal.subject
    ) if collection else []
    return {"count": len(items or []), "items": items or []}


@app.delete("/v1/documents/{document_id}", status_code=204)
def delete_document(document_id: str, principal: Principal = Depends(current_user)) -> Response:
    if not pipeline.delete_scoped_document(document_id, principal.tenant_id, principal.subject):
        raise HTTPException(status_code=404, detail={"code": "DOCUMENT_NOT_FOUND"})
    return Response(status_code=204)


@app.get("/v1/documents/{document_id}/status")
def document_status(document_id: str, principal: Principal = Depends(current_user)) -> dict:
    item = pipeline.store.document_status(document_id, principal.tenant_id, principal.subject)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "DOCUMENT_NOT_FOUND"})
    return item


@app.post("/v1/documents/{document_id}/promote", status_code=201)
def promote_document(document_id: str, body: PromoteRequest,
                     principal: Principal = Depends(current_user)) -> dict:
    promoted = pipeline.promote_temporary_document(
        document_id, body.collection_id, principal.tenant_id, principal.subject
    )
    if not promoted:
        raise HTTPException(status_code=404, detail={"code": "DOCUMENT_NOT_FOUND"})
    return promoted


@app.get("/v1/documents/{document_id}/pdf")
def scoped_pdf(document_id: str, principal: Principal = Depends(current_user)):
    item = pipeline.store.document_for_user(document_id, principal.tenant_id, principal.subject)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "DOCUMENT_NOT_FOUND"})
    path = Path(item["stored_path"] or "").resolve()
    roots = [pipeline.settings.data_dir.resolve(),
             (pipeline.settings.var_dir / "private").resolve(),
             (pipeline.settings.var_dir / "temporary").resolve()]
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise HTTPException(status_code=404, detail={"code": "PDF_UNAVAILABLE"})
    return FileResponse(path, media_type="application/pdf", filename=item["source_name"])


@app.post("/v1/admin/temporary-documents/cleanup", dependencies=[Depends(require_admin)])
def cleanup_temporary_documents() -> dict:
    return pipeline.cleanup_expired_documents()


@app.get("/v1/conversations")
def list_conversations(principal: Principal = Depends(current_user)) -> dict:
    items = pipeline.store.conversations(principal.tenant_id, principal.subject)
    return {"count": len(items), "items": items}


@app.get("/v1/conversations/{conversation_id}")
def get_conversation(conversation_id: str,
                     principal: Principal = Depends(current_user)) -> dict:
    item = pipeline.store.conversation(conversation_id, principal.tenant_id, principal.subject)
    if not item:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
    return item


@app.patch("/v1/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: ConversationRequest,
                        principal: Principal = Depends(current_user)) -> dict:
    if not pipeline.store.rename_conversation(
            conversation_id, principal.tenant_id, principal.subject, body.title.strip()):
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
    return get_conversation(conversation_id, principal)


@app.delete("/v1/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str,
                        principal: Principal = Depends(current_user)) -> Response:
    if not pipeline.delete_conversation(conversation_id, principal.tenant_id, principal.subject):
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
    return Response(status_code=204)


@app.post("/v1/uploads", status_code=202, dependencies=[Depends(require_admin)])
async def upload_pdf(request: Request, force: bool = False) -> dict:
    raw_name = unquote(request.headers.get("x-filename", "upload.pdf"))
    name = Path(raw_name).name
    if name != raw_name or not name.lower().endswith(".pdf") or len(name) > 180:
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_FILENAME_INVALID"})
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/pdf":
        raise HTTPException(status_code=415, detail={"code": "UPLOAD_MIME_INVALID",
                                                    "message": "仅接受 application/pdf"})
    maximum = 50 * 1024 * 1024
    if int(request.headers.get("content-length", "0") or 0) > maximum:
        raise HTTPException(status_code=413, detail={"code": "UPLOAD_TOO_LARGE"})
    content = bytearray()
    async for block in request.stream():
        content.extend(block)
        if len(content) > maximum:
            raise HTTPException(status_code=413, detail={"code": "UPLOAD_TOO_LARGE"})
    if len(content) < 5 or not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_NOT_PDF"})
    try:
        import fitz
        with fitz.open(stream=bytes(content), filetype="pdf") as document:
            if document.needs_pass:
                raise HTTPException(status_code=400, detail={"code": "UPLOAD_PDF_ENCRYPTED",
                                                            "message": "暂不接受需要密码的 PDF"})
            if document.page_count < 1:
                raise HTTPException(status_code=400, detail={"code": "UPLOAD_PDF_EMPTY"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_PDF_INVALID",
                                                    "message": "PDF 结构无效或已损坏"}) from exc
    source_hash = hashlib.sha256(content).hexdigest()
    active = pipeline.store.active_ingestion_for_hash(source_hash)
    if active:
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TASK_ACTIVE", **active})
    existing = pipeline.store.paper_by_hash(source_hash)
    if existing and not force:
        return {"status": "duplicate_existing", "paper_id": existing["paper_id"],
                "message": "相同内容的论文已存在，未创建重复任务"}
    upload_dir = pipeline.settings.var_dir / "uploads"
    destination = upload_dir / uuid.uuid4().hex / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    run_id, request_id = f"run_{uuid.uuid4().hex}", request.state.request_id
    pipeline.store.create_ingestion_job(run_id, request_id, file_path=str(destination), force=force,
                                        source_hash=source_hash)
    request.app.state.worker.notify()
    return {"run_id": run_id, "request_id": request_id, "status": "queued"}


@app.post("/v1/ingestions", status_code=202, dependencies=[Depends(require_admin)])
def create_ingestion(body: IngestionRequest, request: Request) -> dict:
    active = pipeline.store.active_full_ingestion()
    if active:
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TASK_ACTIVE", **active})
    run_id, request_id = f"run_{uuid.uuid4().hex}", request.state.request_id
    pipeline.store.create_ingestion_job(run_id, request_id, file_path=None, force=body.force)
    request.app.state.worker.notify()
    return {"run_id": run_id, "request_id": request_id, "status": "queued"}


@app.post("/v1/runs/{run_id}/retry", status_code=202, dependencies=[Depends(require_admin)])
def retry_run(run_id: str, request: Request) -> dict:
    previous, job = pipeline.store.run(run_id), pipeline.store.job_for_run(run_id)
    if not previous or not job or previous["status"] not in {"failed", "partial_failed", "interrupted"}:
        raise HTTPException(status_code=409, detail={"code": "RUN_NOT_RETRYABLE"})
    new_run, new_request = f"run_{uuid.uuid4().hex}", f"req_{uuid.uuid4().hex}"
    pipeline.store.create_ingestion_job(new_run, new_request, file_path=job["file_path"],
                                        force=bool(job["force"]), parent_run_id=run_id,
                                        source_hash=job.get("source_hash"),
                                        tenant_id=job.get("tenant_id"), owner_id=job.get("owner_id"),
                                        collection_id=job.get("collection_id"), scope_type=job.get("scope_type"),
                                        conversation_id=job.get("conversation_id"), expires_at=job.get("expires_at"),
                                        document_id=job.get("document_id"))
    request.app.state.worker.notify()
    return {"run_id": new_run, "request_id": new_request, "status": "queued", "parent_run_id": run_id}


@app.get("/v1/runs", dependencies=[Depends(require_admin)])
def list_runs(limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail="invalid limit")
    items = pipeline.store.latest_runs(limit)
    return {"count": len(items), "items": items}


@app.get("/v1/runs/{run_id}", dependencies=[Depends(require_admin)])
def get_run(run_id: str) -> dict:
    result = pipeline.store.run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="run not found")
    return result


@app.post("/v1/query")
def query(body: QueryRequest, request: Request) -> dict:
    principal = principal_from_request(request)
    # Legacy anonymous calls remain official-only. Private or conversation scope
    # always requires a verified principal and ownership is enforced in SQL.
    wants_private_scope = bool(body.collection_ids or body.conversation_id)
    if wants_private_scope:
        require_user(principal)
    conversation = None
    retrieval_question = body.question
    if body.conversation_id:
        conversation = pipeline.store.conversation(
            body.conversation_id, principal.tenant_id, principal.subject
        )
        if not conversation:
            raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND"})
        retrieval_question = ContextualQueryRewriter(
            pipeline.settings.conversation_context_messages
        ).rewrite(body.question, conversation["messages"])
    result = pipeline.query(
        body.question, top_k=body.top_k, paper_ids=body.paper_ids,
        request_id=request.state.request_id,
        tenant_id=principal.tenant_id,
        subject=principal.subject,
        collection_ids=body.collection_ids,
        conversation_id=body.conversation_id,
        include_official=body.include_official,
        retrieval_question=retrieval_question,
    )
    if body.conversation_id:
        pipeline.store.add_message(
            body.conversation_id, principal.tenant_id, principal.subject, "user", body.question,
            request_id=result.request_id, original_question=body.question,
            retrieval_question=retrieval_question,
        )
        pipeline.store.add_message(
            body.conversation_id, principal.tenant_id, principal.subject, "assistant", result.answer,
            request_id=result.request_id, citations=[item.to_dict() for item in result.evidence],
        )
    return result.to_dict()


@app.post("/v1/retrieve")
def retrieve(body: QueryRequest, request: Request) -> dict:
    principal = principal_from_request(request)
    if body.collection_ids or body.conversation_id:
        require_user(principal)
    return pipeline.retrieve(
        body.question, top_k=body.top_k, tenant_id=principal.tenant_id,
        subject=principal.subject, collection_ids=body.collection_ids,
        conversation_id=body.conversation_id, include_official=body.include_official,
        paper_ids=body.paper_ids,
        request_id=request.state.request_id,
    )


@app.get("/v1/evidence/{evidence_id}")
def evidence_detail(evidence_id: str, request: Request,
                    conversation_id: str | None = None) -> dict:
    principal = principal_from_request(request)
    item = pipeline.store.evidence_for_user(
        evidence_id, principal.tenant_id, principal.subject,
        conversation_id=conversation_id,
    )
    if not item:
        raise HTTPException(status_code=404, detail={"code": "EVIDENCE_NOT_FOUND"})
    return item


@app.get("/v1/queries/{request_id}", dependencies=[Depends(require_admin)])
def query_diagnostic(request_id: str) -> dict:
    result = pipeline.store.query_diagnostic(request_id)
    if not result:
        raise HTTPException(status_code=404, detail="query not found")
    return result


@app.get("/v1/queries", dependencies=[Depends(require_admin)])
def list_query_runs(limit: int = 20) -> dict:
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=400, detail={"code": "PAGINATION_INVALID"})
    items = pipeline.store.latest_query_runs(limit)
    return {"count": len(items), "items": items}


@app.get("/v1/extractions/schema", dependencies=[Depends(require_admin)])
def extraction_schema() -> dict:
    return EXTRACTION_JSON_SCHEMA


@app.get("/v1/extractions/{extraction_run_id}", dependencies=[Depends(require_admin)])
def extraction_detail(extraction_run_id: str) -> dict:
    result = pipeline.store.extraction(extraction_run_id)
    if not result:
        raise HTTPException(status_code=404, detail="extraction not found")
    return result


@app.get("/v1/papers")
def papers(limit: int = 50, offset: int = 0, status: str | None = None,
           q: str | None = None) -> dict:
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(status_code=400, detail={"code": "PAGINATION_INVALID"})
    items = pipeline.store.official_papers()
    if status:
        if status not in {"ready", "partial_failed", "failed", "processing"}:
            raise HTTPException(status_code=400, detail={"code": "PAPER_STATUS_INVALID"})
        items = [item for item in items if item["status"] == status]
    if q:
        needle = q.casefold().strip()
        items = [item for item in items if needle in item["file_name"].casefold()]
    total = len(items)
    return {"count": total, "limit": limit, "offset": offset,
            "items": [public_paper(item) for item in items[offset:offset + limit]]}


@app.get("/v1/papers/{paper_id}")
def paper_detail(paper_id: str) -> dict:
    if not re.fullmatch(r"paper_[a-f0-9]{24}", paper_id):
        raise HTTPException(status_code=400, detail="invalid paper_id")
    item = pipeline.store.official_paper(paper_id)
    if not item:
        raise HTTPException(status_code=404, detail="paper not found")
    return public_paper(item)


@app.post("/v1/papers/{paper_id}/retry", status_code=202, dependencies=[Depends(require_admin)])
def retry_paper(paper_id: str, request: Request) -> dict:
    item = checked_paper(paper_id)
    if item["status"] not in {"failed", "partial_failed"}:
        raise HTTPException(status_code=409, detail={"code": "PAPER_NOT_RETRYABLE"})
    active = pipeline.store.active_ingestion_for_hash(item["source_hash"])
    if active:
        raise HTTPException(status_code=409, detail={"code": "DUPLICATE_TASK_ACTIVE", **active})
    path = Path(item["file_path"]).resolve()
    roots = [pipeline.settings.data_dir.resolve(), (pipeline.settings.var_dir / "uploads").resolve()]
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise HTTPException(status_code=409, detail={"code": "PAPER_SOURCE_UNAVAILABLE"})
    run_id = f"run_{uuid.uuid4().hex}"
    pipeline.store.create_ingestion_job(run_id, request.state.request_id, file_path=str(path),
                                        force=True, source_hash=item["source_hash"])
    request.app.state.worker.notify()
    return {"run_id": run_id, "request_id": request.state.request_id, "status": "queued",
            "paper_id": paper_id}


@app.get("/v1/papers/{paper_id}/pages", dependencies=[Depends(require_admin)])
def paper_pages(paper_id: str) -> dict:
    checked_paper(paper_id)
    items = pipeline.store.pages(paper_id)
    return {"count": len(items), "items": items}


@app.get("/v1/papers/{paper_id}/chunks", dependencies=[Depends(require_admin)])
def paper_chunks(paper_id: str, limit: int = 100, offset: int = 0) -> dict:
    checked_paper(paper_id)
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(status_code=400, detail="invalid pagination")
    items = pipeline.store.paper_chunks(paper_id, limit=limit, offset=offset)
    return {"count": len(items), "items": items, "limit": limit, "offset": offset}


@app.get("/v1/chunks/{chunk_id}", dependencies=[Depends(require_admin)])
def chunk_detail(chunk_id: str) -> dict:
    item = pipeline.store.chunk(chunk_id)
    if not item:
        raise HTTPException(status_code=404, detail="chunk not found")
    return item


@app.get("/v1/papers/{paper_id}/pdf", dependencies=[Depends(require_admin)])
def original_pdf(paper_id: str):
    item = checked_paper(paper_id)
    path = Path(item["file_path"]).resolve()
    roots = [pipeline.settings.data_dir.resolve(), (pipeline.settings.var_dir / "uploads").resolve()]
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise HTTPException(status_code=404, detail="PDF unavailable")
    return FileResponse(path, media_type="application/pdf", filename=item["file_name"])
