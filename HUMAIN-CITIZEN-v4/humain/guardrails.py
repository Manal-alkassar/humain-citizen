"""Security controls: injection screening, PII redaction, tool allow-list,
argument schema validation and consent tokens."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets

# ------------------------------------------------------------ injection
INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (your|the|all) (rules|instructions|policy|policies)",
    r"\b(system|developer) prompt\b", r"\byou are now\b", r"\bact as (an? )?(admin|administrator|officer|root)\b",
    r"\bdeveloper mode\b", r"\bjailbreak\b", r"\bbypass (the )?(checks?|verification|consent|approval)\b",
    r"\bwithout (asking|confirmation|consent|my approval)\b.*\b(transfer|delete|cancel|submit)\b",
    r"\b(submit|approve|transfer|delete|cancel|pay|send)\b.*\bwithout (asking|confirmation|consent|my (approval|consent|permission))",
    r"\btransfer (the )?ownership\b", r"\bdelete (all|the) (records?|logs?|audit)\b",
    r"\b(show|give|list|reveal|dump) (me )?(all |the )?(national ids?|citizens?|other users?|database|records of)\b",
    r"\bcall (the )?tool\b", r"\bexecute\s*\(", r"<\s*script", r"\{\s*\"tool\"\s*:",
    r"ignore (all |any |the |your )*(polic(y|ies)|rules|guardrails|security|restrictions)",
    r"\bdirectly (call|access|query|use) (the )?(government |gov )?(database|db|api|apis|system|backend)",
    r"تجاهل (كل )?التعليمات", r"تجاهل (كل )?(السياسات|الانظمه|القواعد)", r"انقل الملكيه", r"انقل ملكيه", r"احذف السجلات", r"بدون موافقت",
]
_INJ = [re.compile(p, re.I) for p in INJECTION_PATTERNS]


def screen_input(text: str) -> list[str]:
    """Return the list of matched injection signatures (empty = clean)."""
    from .nlu import normalise
    t = text or ""
    tn = normalise(t)
    return [p.pattern for p in _INJ if p.search(t) or p.search(tn)]


# ------------------------------------------------------------ PII redaction
_PII = [
    (re.compile(r"\b([12])\d{5}(\d{4})\b"), r"\1******\2"),            # national id / iqama (10 digits)
    (re.compile(r"\+?966\s?5\d(\d{3})(\d{4})"), r"+9665X***\2"),        # Saudi mobile
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?(\d{4})\b"), r"****-****-****-\1"),  # card numbers
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<email>"),
]


def redact(value):
    """Recursively redact PII from strings / dicts / lists before logging."""
    if isinstance(value, str):
        for rx, rep in _PII:
            value = rx.sub(rep, value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def mask_id(nid: str) -> str:
    return redact(nid)


# ------------------------------------------------------------ tool registry / allow-list
PLATE_RE = r"^[A-Z]{3} \d{4}$"
NID_RE = r"^[12]\d{9}$"

TOOL_SCHEMAS = {
    "police.file_theft_report": {"required": {"owner_nid": NID_RE, "plate": PLATE_RE, "incident_time": r"^.{2,80}$",
                                              "incident_location": r"^.{2,80}$"}},
    "registry.flag_vehicle": {"required": {"plate": PLATE_RE, "police_ref": r"^POL-\d{6}$"}},
    "insurer.notify_claim": {"required": {"plate": PLATE_RE, "police_ref": r"^POL-\d{6}$", "insurer": r"^.{2,60}$"}},
    "payment.charge_fee": {"required": {"payer_nid": NID_RE, "amount_sar": r"^\d{1,5}$", "purpose": r"^[A-Z_]{3,40}$"}},
    "traffic.issue_replacement": {"required": {"holder_nid": NID_RE, "licence_no": r"^L-\d{8}$",
                                               "reason": r"^(lost|stolen|damaged)$", "delivery": r"^(pickup|delivery)$",
                                               "payment_ref": r"^PAY-\d{6}$"}},
    "commerce.cancel_cr": {"required": {"owner_nid": NID_RE, "cr": r"^\d{10}$", "reason": r"^.{2,200}$"}},
    "hr.create_contract": {"required": {"employer_cr": r"^\d{10}$", "employee": r"^.{2,80}$", "job_title": r"^.{2,80}$", "salary": r"^\d+ SAR$"}},
    "hr.route_to_officer": {"required": {"employer_nid": NID_RE, "employee": r"^.{2,80}$", "reason": r"^.{2,200}$"}},
    "hr.draft_resignation": {"required": {"worker_nid": NID_RE, "employer": r"^.{2,80}$", "last_day": r"^.{2,60}$"}},
    # read-only tools (verification + status): no side effects, still allow-listed and schema-checked
    "case.get_status": {"required": {"ref": r"^[A-Z]{2,4}-\d{6}$"}},
    "identity.verify_identity": {"required": {"nid": NID_RE}},
    "vehicle.get_user_vehicles": {"required": {"nid": NID_RE}},
    "vehicle.verify_vehicle": {"required": {"nid": NID_RE, "plate": PLATE_RE}},
    "commerce.verify_registration": {"required": {"nid": NID_RE, "cr": r"^\d{10}$"}},
    "traffic.check_licence_status": {"required": {"nid": NID_RE}},
    "traffic.find_branch_slot": {"required": {"nid": NID_RE}},
    "hr.verify_employee": {"required": {"employer_cr": r"^\d{10}$", "employee": r"^.{2,80}$"}},
    "hr.check_existing_contract": {"required": {"employer_cr": r"^\d{10}$", "employee": r"^.{2,80}$"}},
}
READ_ONLY = frozenset({"case.get_status", "identity.verify_identity", "vehicle.get_user_vehicles", "vehicle.verify_vehicle",
                       "commerce.verify_registration", "traffic.check_licence_status", "traffic.find_branch_slot",
                       "hr.verify_employee", "hr.check_existing_contract"})

GLOBAL_ALLOW_LIST = frozenset(TOOL_SCHEMAS)


class GuardrailViolation(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def check_tool_call(tool: str, args: dict, journey_tools: set[str]) -> None:
    """Allow-list + schema validation + data-minimisation. Raises on violation."""
    if tool not in GLOBAL_ALLOW_LIST:
        raise GuardrailViolation("TOOL_NOT_ALLOWLISTED", tool)
    if tool not in journey_tools and tool not in READ_ONLY:
        raise GuardrailViolation("TOOL_OUT_OF_SCOPE", f"{tool} is not part of the active journey")
    schema = TOOL_SCHEMAS[tool]["required"]
    extra = set(args) - set(schema)
    if extra:
        raise GuardrailViolation("DATA_MINIMISATION", f"unexpected fields {sorted(extra)}")
    for field, rx in schema.items():
        v = args.get(field)
        if v is None or not re.match(rx, str(v)):
            raise GuardrailViolation("SCHEMA_INVALID", f"{tool}.{field}={redact(str(v))!r}")


# ------------------------------------------------------------ consent tokens
_SECRET = secrets.token_bytes(32)  # kept outside model context; per-process in the prototype


def plan_hash(case_id: str, plan: list[dict]) -> str:
    blob = json.dumps({"case": case_id, "plan": [(s["tool"], s.get("args")) for s in plan]}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def issue_consent(case_id: str, phash: str) -> str:
    return hmac.new(_SECRET, f"{case_id}:{phash}".encode(), "sha256").hexdigest()[:24]


def verify_consent(token: str | None, case_id: str, phash: str) -> bool:
    return bool(token) and hmac.compare_digest(token, issue_consent(case_id, phash))


# ------------------------------------------------------------ deterministic authorization
# The authenticated server-side session is the ONLY source of identity. Values typed by the citizen,
# proposed by a model, or sent by a frontend never override it. This layer cannot be talked out of a decision.
def authorize(tool: str, args: dict, session_nid: str, record: dict) -> tuple[bool, str, str]:
    """Return (allowed, code, explanation). `record` is the authenticated citizen's own record."""
    if not session_nid or not record:
        return False, "UNAUTHENTICATED", "no authenticated session"
    for k, v in args.items():
        if k == "nid" or k.endswith("_nid"):
            if v != session_nid:
                return False, "SCOPE_DENIED", f"{k} does not match the authenticated citizen"
    if tool in READ_ONLY:
        return True, "OK", "read-only, citizen scope matches"
    if "plate" in args and args["plate"] not in {v["plate"] for v in record.get("vehicles", [])}:
        return False, "BOLA_DENIED", "vehicle.owner_id != session.citizen_id"
    for k in ("cr", "employer_cr"):
        if k in args and args[k] not in {c["cr"] for c in record.get("commercial_registrations", [])}:
            return False, "BOLA_DENIED", "registration.owner_id != session.citizen_id"
    if "licence_no" in args and args["licence_no"] != record.get("licence", {}).get("number"):
        return False, "BOLA_DENIED", "licence.holder_id != session.citizen_id"
    return True, "OK", "owner_id == session.citizen_id for every object"
