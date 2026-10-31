"""Agent 2 — Security Agent.

Answers one question: "Is this proposed action safe and permitted?"
It reviews the Planning Agent's ACTION PLAN independently (it does not trust the planner's reasoning)
and returns ALLOW / BLOCK / ESCALATE with the checks it ran.

It is NOT the last line of defence: every tool call is still enforced by the deterministic
authorization layer (guardrails.authorize) and the gateway (allow-list + schema), which no agent
or model can override. In this prototype the reviewer is rule-based and fully explainable.
"""
from __future__ import annotations

from .guardrails import GLOBAL_ALLOW_LIST, TOOL_SCHEMAS, authorize, screen_input
from .policy import RULES

ROLE_FOR = {"report_stolen_vehicle": "citizen", "replace_lost_licence": "citizen", "leave_job": "citizen",
            "close_business": "establishment_owner", "hire_employee": "establishment_owner",
            "end_employee_contract": "establishment_owner"}
FREE_TEXT = ("incident_time", "incident_location", "reason", "employee", "job_title", "last_day")


def review(plan: list[dict], policy: dict, intent: str, session: dict, record: dict, slots: dict, backends,
           guard_events: int) -> dict:
    checks = []

    def add(name, ok, detail, hard=True):
        checks.append({"check": name, "result": "pass" if ok else ("FAIL" if hard else "WARN"), "detail": detail})

    nid = session["nid"]
    add("authenticated_identity", bool(record.get("identity_verified")), "session identity from host-app login (mock SSO)")
    scope_ok = all(v == nid for s in plan for k, v in s["args"].items() if k == "nid" or k.endswith("_nid"))
    add("citizen_scope", scope_ok, "every *_nid argument equals the authenticated citizen")
    bola = [authorize(s["tool"], {k: v for k, v in s["args"].items() if not str(v).startswith("$")}, nid, record) for s in plan]
    add("object_ownership_BOLA", all(b[0] for b in bola), "; ".join(sorted({b[2] for b in bola})))
    bopla = all(set(s["args"]) <= set(TOOL_SCHEMAS.get(s["tool"], {}).get("required", {})) for s in plan)
    add("property_level_BOPLA", bopla, "only the properties each service needs are sent")
    allowed_tools = {s["tool"] for s in policy["steps"]}
    add("function_authorization_BFLA", all(s["tool"] in allowed_tools for s in plan), f"tools limited to {sorted(allowed_tools)}")
    role = ROLE_FOR.get(intent, "citizen")
    has_role = role == "citizen" or bool(record.get("commercial_registrations"))
    add("rbac_role", has_role, f"required role: {role}")
    elig = all(RULES[r][0](record, slots, backends) for r in policy["eligibility"])
    add("policy_requirements", elig, f"{policy['policy_id']} v{policy['version']} eligibility rules")
    add("tool_permissions", all(s["tool"] in GLOBAL_ALLOW_LIST for s in plan), "all tools on the global allow-list")
    texts = [str(v) for s in plan for k, v in s["args"].items() if k in FREE_TEXT]
    inj = [t for t in texts if screen_input(t)]
    add("prompt_injection", not inj, "free-text fields screened" if not inj else f"suspicious text in {len(inj)} field(s)")
    add("risk_level", True, f"policy risk: {policy['risk']}", hard=False)
    needs = "citizen confirmation" + (" + step-up authentication" if policy.get("step_up_auth") else "")
    add("human_confirmation", True, f"required before execution: {needs}")
    add("data_access_scope", True, "only the authenticated citizen's own record is used")
    suspicious = guard_events >= 3
    add("suspicious_behaviour", not suspicious, f"{guard_events} blocked attempt(s) this session", hard=False)

    hard_fail = [c["check"] for c in checks if c["result"] == "FAIL"]
    if hard_fail:
        decision, reason = "BLOCK", ", ".join(hard_fail)
    elif suspicious:
        decision, reason = "ESCALATE", "repeated blocked attempts in this session"
    elif policy["autonomy"] == "human_review":
        decision, reason = "ESCALATE", "legally sensitive: only routing to a human officer is permitted"
    else:
        decision, reason = "ALLOW", f"{len(checks)} checks passed · {needs} required"
    return {"decision": decision, "reason": reason, "checks": checks, "requires": needs, "risk": policy["risk"]}
