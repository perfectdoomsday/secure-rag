import re
from dataclasses import dataclass
from typing import FrozenSet

ROLE_PERMISSIONS: dict[str, FrozenSet[str]] = {
    "employee": frozenset({"general", "policies"}),
    "finance": frozenset({"general", "policies", "finance"}),
    "hr": frozenset({"general", "policies", "hr"}),
    "marketing": frozenset({"general", "policies", "marketing"}),
    "executive": frozenset({"general", "policies", "finance", "hr", "marketing", "legal", "operations", "sales", "engineering"}),
    "admin": frozenset({"general", "policies", "finance", "hr", "marketing", "legal", "operations", "sales", "engineering"}),
}
PRIVILEGED = {"executive", "admin"}
DEMO_USERS = {"alice": "employee", "finn": "finance", "hannah": "hr", "maya": "marketing", "ceo": "executive", "admin": "admin"}

@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    @property
    def departments(self) -> FrozenSet[str]:
        return ROLE_PERMISSIONS.get(self.role, frozenset())
    @property
    def privileged(self) -> bool:
        return self.role in PRIVILEGED

def principal_from_dev_headers(user: str | None, role: str | None) -> Principal:
    """DEVELOPMENT ONLY. Production must validate JWT/OIDC and derive roles server-side."""
    if not user:
        raise ValueError("Missing identity")
    actual = DEMO_USERS.get(user)
    if not actual or (role and role != actual):
        raise ValueError("Invalid demo identity or role mismatch")
    return Principal(user_id=user, role=actual)

def can_access_department(p: Principal, department: str) -> bool:
    return p.privileged or department in p.departments

def pii_redact(text: str) -> tuple[str, list[str]]:
    """Conservative baseline redaction. Extend and validate for your data types/jurisdictions."""
    found = []
    patterns = [
        (r'\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b', "EMAIL"),
        (r'\b(?:\+?91[- ]?)?[6-9]\d{9}\b', "PHONE"),
        (r'\b\d{12}\b', "POSSIBLE_ID_NUMBER"),
        (r'\b\d{3}-\d{2}-\d{4}\b', "SSN"),
    ]
    out = text
    for pattern, label in patterns:
        matches = re.findall(pattern, out)
        if matches:
            found.extend([label] * len(matches))
            out = re.sub(pattern, f"[REDACTED_{label}]", out)
    return out, found

INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"reveal (the )?(system prompt|hidden instructions|secrets)",
    r"act as (the )?(system|developer) prompt",
    r"bypass (the )?(security|access controls|guardrails)",
]
OUT_OF_SCOPE = ["write malware", "steal credentials", "exploit a public target", "build a bomb"]

def guard_query(query: str) -> tuple[bool, str, str]:
    q = query.strip()
    if not q:
        return False, "empty_query", q
    if len(q) > 2000:
        return False, "query_too_long", q[:2000]
    lowered = q.lower()
    if any(re.search(p, lowered) for p in INJECTION_PATTERNS):
        return False, "prompt_injection", q
    if any(term in lowered for term in OUT_OF_SCOPE):
        return False, "out_of_scope", q
    safe, _ = pii_redact(q)
    return True, "ok", safe
