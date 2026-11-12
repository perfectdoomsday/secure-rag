# Production readiness checklist

## Identity and authorization
- [ ] Replace `X-User`/`X-Role` demo headers with Microsoft Entra ID / OIDC JWT validation.
- [ ] Derive roles from verified claims/groups on the server; reject client role overrides.
- [ ] Add tenant ID to every point and every mandatory filter if multi-tenant.
- [ ] Define and test policy for group changes, document ACL inheritance, revocation and deleted users.
- [ ] Add authorization tests for every role × department × classification and adversarial filter bypass attempts.

## Data protection
- [ ] Use managed identity/secret store; rotate keys; no secrets in images or logs.
- [ ] Encrypt traffic and storage; define retention, deletion, backups and restore tests.
- [ ] Add robust PII/DLP detection, allowlisted file types, malware scanning and parser sandboxing.
- [ ] Validate uploaded file size, decompression bombs, MIME/file signatures and parser timeouts.
- [ ] Ensure document deletion removes all vectors/chunks and invalidates caches.

## Quality and safety
- [ ] Create versioned, held-out test set with expected answers, source evidence and access labels.
- [ ] Evaluate retrieval Recall@k/MRR, faithfulness, answer relevancy, citation correctness, abstention and latency.
- [ ] Add Ragas run in CI with thresholds and dataset versioning; human-review critical failures.
- [ ] Test prompt injection in user queries and retrieved documents, PII leakage, jailbreaks and data exfiltration.
- [ ] Require human approval for actions; do not let this read-only assistant perform write actions.

## Operations and cost
- [ ] Export OpenTelemetry traces to Azure Monitor / Application Insights.
- [ ] Alert on elevated 5xx, latency, empty retrieval, access denials, eval regressions and daily cost.
- [ ] Persist usage ledger in a database; current starter cost gauge is process-local and resets on restart.
- [ ] Set per-user, per-team and global rate/token limits; define provider spend caps.
- [ ] Use pinned dependency versions, image scanning, SBOM, secret scanning and signed builds.
- [ ] Deploy behind private networking/API gateway; TLS, WAF and private vector database endpoint.
- [ ] Canary deploy, run post-deploy smoke/evals, monitor and rollback on failed thresholds.
