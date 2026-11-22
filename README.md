# Secure RAG
Security-first internal knowledge assistant with FastAPI, LangChain, Qdrant, Streamlit, RBAC, guardrails, audit logs, Prometheus metrics, and CI evaluation gates.
Qdrant's default HTTP API uses port 6333, so the new container cannot bind to it. Port 6333 was already being used by another process or Docker container, so changed it to 6334.