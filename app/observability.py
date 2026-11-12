import json, logging, time, uuid
from contextlib import contextmanager
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("secure_rag")
REQUESTS = Counter("rag_requests_total", "RAG requests", ["status", "role"])
LATENCY = Histogram("rag_request_duration_seconds", "RAG request latency")
TOKENS = Counter("rag_estimated_tokens_total", "Estimated tokens", ["kind"])
COST = Counter("rag_estimated_cost_usd_total", "Estimated model cost in USD")
DENIALS = Counter("rag_access_denials_total", "Authorization/guardrail denials", ["reason"])
DAILY_COST = Gauge("rag_daily_estimated_cost_usd", "Process-local daily estimated cost (resets with process)")

def audit(event: str, **fields):
    logger.info(json.dumps({"event": event, "timestamp": time.time(), **fields}, default=str))

@contextmanager
def timed():
    start = time.perf_counter()
    try:
        yield
    finally:
        LATENCY.observe(time.perf_counter() - start)

def metrics_response():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
