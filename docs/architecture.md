# Architecture and design notes

## Request path
1. Resolve authenticated identity (demo headers in this starter only; production OIDC/JWT).
2. Validate query length and block obvious prompt-injection / disallowed requests.
3. Redact detected PII from the user query.
4. Retrieve with a server-created Qdrant filter for `allowed_roles` and permitted departments. Retrieval errors fail closed; never retry without the filter.
5. Re-check returned metadata in application code as defense in depth.
6. Redact PII in retrieved passages before model invocation.
7. Ask the model to use only the supplied context and cite source IDs; abstain when evidence is insufficient.
8. Emit audit event, request metrics, token estimates and estimated cost.

## Data model per chunk
`filename`, `department`, `classification`, `allowed_roles`, `uploaded_by`, `document_id`, `start_index` and vector embedding. Production should add immutable source IDs, content hash, tenant ID, ACL version, retention metadata, document version and ingestion timestamp.

## Key design choices
- **Deny by default:** no caller-supplied role in production; roles come from verified identity claims and server-side group mapping.
- **Authorization before retrieval:** query filter restricts candidates. A post-retrieval ACL check is a second barrier, not the primary one.
- **No unfiltered fallback:** vector database or filter errors return service-unavailable.
- **Untrusted retrieval:** retrieved text can contain prompt injection. Delimit it, instruct the model not to follow it, and never give the model tools that can access arbitrary data.
- **Least privilege ingestion:** only trusted admins set ACL metadata. Production ingestion should require approved source connectors and ACL inheritance from the source system.
- **No secrets in logs:** log IDs, roles, source IDs, latency and token counts; avoid raw prompts, full chunks and model responses unless an explicit approved sampling policy exists.

## Scale-up path
Separate API and ingestion workers; use a durable queue, idempotent ingestion jobs, retry/dead-letter handling, content-hash deduplication, document-version tombstones and periodic orphan cleanup. Consider hybrid sparse+dense retrieval, reranking, query rewriting, per-user rate limits and tenant-aware indexes when evaluation justifies them.
