import logging
import os
import tempfile
import time
import uuid
from datetime import date

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    UploadFile,
    File,
    Form,
    Depends,
    Request,
)
from pydantic import BaseModel, Field

from app.config import settings
from app.security import (
    Principal,
    principal_from_dev_headers,
    can_access_department,
    guard_query,
    pii_redact,
)
from app.rag import ingest_text, retrieve, answer
from app.observability import (
    audit,
    REQUESTS,
    TOKENS,
    COST,
    DAILY_COST,
    DENIALS,
    timed,
    metrics_response,
)


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------

app = FastAPI(
    title="SecureRAG",
    version="0.1.0",
    description="Security-first internal RAG starter",
)

_daily = {
    "date": str(date.today()),
    "cost": 0.0,
}


# ---------------------------------------------------------------------
# Request and response models
# ---------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)


class AskResponse(BaseModel):
    answer: str
    sources: list[dict]
    request_id: str
    estimated_cost_usd: float
    guardrail: str


# ---------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------

def get_principal(
    x_user: str | None = Header(default=None),
    x_role: str | None = Header(default=None),
) -> Principal:
    try:
        return principal_from_dev_headers(x_user, x_role)

    except ValueError as e:
        raise HTTPException(
            status_code=401,
            detail=str(e),
        )


# ---------------------------------------------------------------------
# Request middleware
# ---------------------------------------------------------------------

@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get(
        "x-request-id",
        str(uuid.uuid4()),
    )

    request.state.request_id = request_id
    start = time.perf_counter()

    try:
        response = await call_next(request)

        response.headers["X-Request-ID"] = request_id

        return response

    finally:
        audit(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            duration_ms=round(
                (time.perf_counter() - start) * 1000,
                2,
            ),
        )


# ---------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "environment": settings.app_env,
    }


# ---------------------------------------------------------------------
# Prometheus metrics endpoint
# ---------------------------------------------------------------------

@app.get("/metrics", include_in_schema=False)
def metrics():
    return metrics_response()


# ---------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------

@app.post("/chat", response_model=AskResponse)
def chat(
    body: AskRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
):
    rid = request.state.request_id

    # Step 1: Query guardrails
    allowed, reason, safe_query = guard_query(body.question)

    if not allowed:
        DENIALS.labels(reason=reason).inc()

        REQUESTS.labels(
            status="blocked",
            role=principal.role,
        ).inc()

        audit(
            "guardrail_block",
            request_id=rid,
            user_id=principal.user_id,
            role=principal.role,
            reason=reason,
        )

        return AskResponse(
            answer=(
                "I can't help with that request. "
                "Please ask a question about authorized company information."
            ),
            sources=[],
            request_id=rid,
            estimated_cost_usd=0.0,
            guardrail=reason,
        )

    # Step 2: Redact PII from the query
    redacted_query, query_pii = pii_redact(safe_query)

    # Step 3: Retrieve documents and generate an answer
    try:
        with timed():

            docs = retrieve(
                redacted_query,
                principal,
                body.top_k,
            )

            # Defense-in-depth authorization check
            # on returned document metadata.
            secure_docs = []

            for d in docs:
                roles = d.metadata.get(
                    "allowed_roles",
                    [],
                )

                dept = d.metadata.get(
                    "department",
                    "",
                )

                if (
                    principal.role in roles
                    and can_access_department(principal, dept)
                ):
                    secure_docs.append(d)

                else:
                    DENIALS.labels(
                        reason="retrieval_acl_mismatch"
                    ).inc()

                    audit(
                        "retrieval_acl_mismatch",
                        request_id=rid,
                        user_id=principal.user_id,
                        role=principal.role,
                        document=d.metadata.get("filename"),
                    )

            # Step 4: Redact PII from retrieved document content
            # before sending context to the LLM.
            for d in secure_docs:
                d.page_content, _ = pii_redact(
                    d.page_content
                )

            # Step 5: Generate the answer
            response, usage = answer(
                redacted_query,
                secure_docs,
            )

    except Exception:
        logger.exception(
            "RAG chat failed | request_id=%s | user_id=%s | role=%s",
            rid,
            principal.user_id,
            principal.role,
        )

        REQUESTS.labels(
            status="error",
            role=principal.role,
        ).inc()

        audit(
            "rag_error",
            request_id=rid,
            user_id=principal.user_id,
            role=principal.role,
        )

        raise HTTPException(
            status_code=503,
            detail="Knowledge service temporarily unavailable",
        )

    # Step 6: Calculate estimated API cost
    input_tokens = usage["input"]
    output_tokens = usage["output"]

    cost = (
        input_tokens * settings.cost_input_per_1m / 1_000_000
        + output_tokens * settings.cost_output_per_1m / 1_000_000
    )

    # Step 7: Update daily cost tracking
    if _daily["date"] != str(date.today()):
        _daily.update(
            date=str(date.today()),
            cost=0.0,
        )

    _daily["cost"] += cost

    DAILY_COST.set(_daily["cost"])
    COST.inc(cost)

    TOKENS.labels(kind="input").inc(input_tokens)
    TOKENS.labels(kind="output").inc(output_tokens)

    # Step 8: Cost threshold alert
    if _daily["cost"] >= settings.daily_cost_alert_usd:
        audit(
            "cost_threshold_exceeded",
            daily_cost_usd=round(_daily["cost"], 6),
            threshold=settings.daily_cost_alert_usd,
        )

    # Step 9: Record successful request
    REQUESTS.labels(
        status="ok",
        role=principal.role,
    ).inc()

    # Step 10: Prepare source information
    sources = [
        {
            "filename": d.metadata.get(
                "filename",
                "unknown",
            ),
            "department": d.metadata.get(
                "department",
                "unknown",
            ),
            "classification": d.metadata.get(
                "classification",
                "internal",
            ),
            "excerpt": d.page_content[:500],
        }
        for d in secure_docs
    ]

    # Step 11: Audit successful completion
    audit(
        "chat_complete",
        request_id=rid,
        user_id=principal.user_id,
        role=principal.role,
        source_count=len(sources),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=round(cost, 8),
        query_pii_types=query_pii,
    )

    return AskResponse(
        answer=response,
        sources=sources,
        request_id=rid,
        estimated_cost_usd=round(cost, 8),
        guardrail=(
            "pii_redacted"
            if query_pii
            else "passed"
        ),
    )


# ---------------------------------------------------------------------
# Admin document ingestion endpoint
# ---------------------------------------------------------------------

@app.post("/admin/ingest")
async def ingest(
    request: Request,
    file: UploadFile = File(...),
    department: str = Form(...),
    classification: str = Form("internal"),
    allowed_roles_csv: str = Form(
        "employee,finance,hr,marketing,executive,admin"
    ),
    principal: Principal = Depends(get_principal),
):
    rid = request.state.request_id

    # Step 1: Verify administrator role
    if principal.role != "admin":
        DENIALS.labels(
            reason="ingest_not_admin"
        ).inc()

        raise HTTPException(
            status_code=403,
            detail="Admin role required",
        )

    # Step 2: Validate department access
    if not can_access_department(
        principal,
        department,
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid department",
        )

    # Step 3: Read uploaded document with size limit
    max_upload_bytes = (
        settings.max_upload_mb * 1024 * 1024
    )

    data = await file.read(
        max_upload_bytes + 1
    )

    if len(data) > max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail="File too large",
        )

    # Step 4: Extract text from the uploaded document
    if (
        file.filename
        and file.filename.lower().endswith(".txt")
    ):
        text = data.decode(
            "utf-8",
            errors="replace",
        )

    else:
        try:
            from docling.document_converter import DocumentConverter

            suffix = "." + (
                file.filename or "file.pdf"
            ).split(".")[-1]

            with tempfile.NamedTemporaryFile(
                suffix=suffix,
                delete=False,
            ) as tmp:
                tmp.write(data)
                path = tmp.name

            try:
                text = (
                    DocumentConverter()
                    .convert(path)
                    .document
                    .export_to_markdown()
                )

            finally:
                os.unlink(path)

        except Exception:
            logger.exception(
                "Document extraction failed | request_id=%s | filename=%s",
                rid,
                file.filename,
            )

            raise HTTPException(
                status_code=415,
                detail=(
                    "Unsupported file or Docling unavailable. "
                    "Use .txt or install Docling."
                ),
            )

    # Step 5: Validate document access roles
    roles = sorted(
        set(
            x.strip()
            for x in allowed_roles_csv.split(",")
            if x.strip()
        )
    )

    known = {
        "employee",
        "finance",
        "hr",
        "marketing",
        "executive",
        "admin",
    }

    if not roles or not set(roles).issubset(known):
        raise HTTPException(
            status_code=400,
            detail="Unknown role in ACL",
        )

    # Step 6: Prevent sensitive department exposure
    if (
        department in {"finance", "hr"}
        and "employee" in roles
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Sensitive department ACL cannot include employee"
            ),
        )

    # Step 7: Ingest document into the knowledge base
    try:
        count = ingest_text(
            text,
            file.filename or "uploaded-file",
            department,
            classification,
            roles,
            principal.user_id,
        )

    except Exception:
        logger.exception(
            "Document ingestion failed | request_id=%s | filename=%s | department=%s | user_id=%s",
            rid,
            file.filename,
            department,
            principal.user_id,
        )

        raise HTTPException(
            status_code=503,
            detail="Ingestion failed",
        )

    # Step 8: Record successful ingestion
    audit(
        "document_ingested",
        request_id=rid,
        user_id=principal.user_id,
        filename=file.filename,
        department=department,
        classification=classification,
        allowed_roles=roles,
        chunks=count,
    )

    return {
        "status": "ingested",
        "chunks": count,
        "filename": file.filename,
    }
