import time, uuid
from datetime import date
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form, Depends, Request
from pydantic import BaseModel, Field
from qdrant_client.http.exceptions import UnexpectedResponse
from app.config import settings
from app.security import Principal, principal_from_dev_headers, can_access_department, guard_query, pii_redact
from app.rag import ingest_text, retrieve, answer
from app.observability import audit, REQUESTS, TOKENS, COST, DAILY_COST, DENIALS, timed, metrics_response

app = FastAPI(title="SecureRAG", version="0.1.0", description="Security-first internal RAG starter")
_daily = {"date": str(date.today()), "cost": 0.0}

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
class AskResponse(BaseModel):
    answer: str
    sources: list[dict]
    request_id: str
    estimated_cost_usd: float
    guardrail: str

def get_principal(x_user: str | None = Header(default=None), x_role: str | None = Header(default=None)) -> Principal:
    try: return principal_from_dev_headers(x_user, x_role)
    except ValueError as e: raise HTTPException(status_code=401, detail=str(e))

@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    request.state.request_id = request_id
    start = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        audit("http_request", request_id=request_id, method=request.method, path=request.url.path, duration_ms=round((time.perf_counter()-start)*1000,2))

@app.get("/health")
def health(): return {"status": "ok", "environment": settings.app_env}

@app.get("/metrics", include_in_schema=False)
def metrics(): return metrics_response()

@app.post("/chat", response_model=AskResponse)
def chat(body: AskRequest, request: Request, principal: Principal = Depends(get_principal)):
    rid = request.state.request_id
    allowed, reason, safe_query = guard_query(body.question)
    if not allowed:
        DENIALS.labels(reason=reason).inc(); REQUESTS.labels(status="blocked", role=principal.role).inc()
        audit("guardrail_block", request_id=rid, user_id=principal.user_id, role=principal.role, reason=reason)
        return AskResponse(answer="I can't help with that request. Please ask a question about authorized company information.", sources=[], request_id=rid, estimated_cost_usd=0.0, guardrail=reason)
    redacted_query, query_pii = pii_redact(safe_query)
    with timed():
        try:
            docs = retrieve(redacted_query, principal, body.top_k)
            # Defense-in-depth authorization check on returned metadata.
            secure_docs = []
            for d in docs:
                roles = d.metadata.get("allowed_roles", [])
                dept = d.metadata.get("department", "")
                if principal.role in roles and can_access_department(principal, dept): secure_docs.append(d)
                else:
                    DENIALS.labels(reason="retrieval_acl_mismatch").inc()
                    audit("retrieval_acl_mismatch", request_id=rid, user_id=principal.user_id, role=principal.role, document=d.metadata.get("filename"))
            # Redact PII from retrieved text before sending context to the model.
            for d in secure_docs:
                d.page_content, _ = pii_redact(d.page_content)
            response, usage = answer(redacted_query, secure_docs)
        except Exception:
            REQUESTS.labels(status="error", role=principal.role).inc()
            audit("rag_error", request_id=rid, user_id=principal.user_id, role=principal.role)
            raise HTTPException(status_code=503, detail="Knowledge service temporarily unavailable")
    input_tokens, output_tokens = usage["input"], usage["output"]
    cost = input_tokens * settings.cost_input_per_1m / 1_000_000 + output_tokens * settings.cost_output_per_1m / 1_000_000
    if _daily["date"] != str(date.today()): _daily.update(date=str(date.today()), cost=0.0)
    _daily["cost"] += cost; DAILY_COST.set(_daily["cost"]); COST.inc(cost)
    TOKENS.labels(kind="input").inc(input_tokens); TOKENS.labels(kind="output").inc(output_tokens)
    if _daily["cost"] >= settings.daily_cost_alert_usd:
        audit("cost_threshold_exceeded", daily_cost_usd=round(_daily["cost"], 6), threshold=settings.daily_cost_alert_usd)
    REQUESTS.labels(status="ok", role=principal.role).inc()
    sources = [{"filename": d.metadata.get("filename", "unknown"), "department": d.metadata.get("department", "unknown"), "classification": d.metadata.get("classification", "internal"), "excerpt": d.page_content[:220]} for d in secure_docs]
    audit("chat_complete", request_id=rid, user_id=principal.user_id, role=principal.role, source_count=len(sources), input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost_usd=round(cost,8), query_pii_types=query_pii)
    return AskResponse(answer=response, sources=sources, request_id=rid, estimated_cost_usd=round(cost,8), guardrail="pii_redacted" if query_pii else "passed")

@app.post("/admin/ingest")
async def ingest(file: UploadFile = File(...), department: str = Form(...), classification: str = Form("internal"), allowed_roles_csv: str = Form("employee,finance,hr,marketing,executive,admin"), principal: Principal = Depends(get_principal)):
    if principal.role != "admin":
        DENIALS.labels(reason="ingest_not_admin").inc(); raise HTTPException(status_code=403, detail="Admin role required")
    if not can_access_department(principal, department): raise HTTPException(status_code=400, detail="Invalid department")
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024: raise HTTPException(status_code=413, detail="File too large")
    if file.filename and file.filename.lower().endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    else:
        try:
            from docling.document_converter import DocumentConverter
            import tempfile, os
            suffix = "." + (file.filename or "file.pdf").split(".")[-1]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data); path = tmp.name
            try: text = DocumentConverter().convert(path).document.export_to_markdown()
            finally: os.unlink(path)
        except Exception:
            raise HTTPException(status_code=415, detail="Unsupported file or Docling unavailable. Use .txt or install Docling.")
    roles = sorted(set(x.strip() for x in allowed_roles_csv.split(",") if x.strip()))
    known = {"employee", "finance", "hr", "marketing", "executive", "admin"}
    if not roles or not set(roles).issubset(known): raise HTTPException(status_code=400, detail="Unknown role in ACL")
    if department in {"finance", "hr"} and "employee" in roles:
        raise HTTPException(status_code=400, detail="Sensitive department ACL cannot include employee")
    try: count = ingest_text(text, file.filename or "uploaded-file", department, classification, roles, principal.user_id)
    except Exception: raise HTTPException(status_code=503, detail="Ingestion failed")
    audit("document_ingested", user_id=principal.user_id, filename=file.filename, department=department, classification=classification, allowed_roles=roles, chunks=count)
    return {"status": "ingested", "chunks": count, "filename": file.filename}
