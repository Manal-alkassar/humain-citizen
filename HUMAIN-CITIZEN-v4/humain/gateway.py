"""Tool/API gateway with mock government back-ends.

Everything the agent does to the outside world goes through ``ResilientClient``:
  allow-list + schema check -> idempotency key -> timeout/retry with backoff
  -> circuit breaker -> structured result or controlled failure.
Mock back-ends support failure injection (timeout, outage, business rejection)
so robustness can be demonstrated and tested reproducibly.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .guardrails import check_tool_call


class ToolTimeout(Exception):
    pass


class ToolUnavailable(Exception):
    pass


class ToolRejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class CircuitOpen(Exception):
    pass


PROGRESSIONS = {
    "police.file_theft_report": ("POL", [(0, "RECEIVED"), (5, "REGISTERED"), (90, "UNDER_INVESTIGATION")]),
    "registry.flag_vehicle": ("REG", [(0, "PENDING"), (3, "FLAGGED")]),
    "insurer.notify_claim": ("INS", [(0, "SENT"), (45, "ACKNOWLEDGED")]),
    "payment.charge_fee": ("PAY", [(0, "PAID")]),
    "traffic.issue_replacement": ("TRF", [(0, "SUBMITTED"), (10, "APPROVED"), (40, "ISSUED"), (120, "DISPATCHED")]),
    "commerce.cancel_cr": ("CRC", [(0, "SUBMITTED"), (15, "UNDER_REVIEW"), (180, "CANCELLED")]),
    "hr.create_contract": ("HRC", [(0, "SENT_TO_EMPLOYEE"), (20, "ACCEPTED_BY_EMPLOYEE"), (40, "ACTIVE")]),
    "hr.route_to_officer": ("HRE", [(0, "QUEUED"), (20, "ASSIGNED")]),
    "hr.draft_resignation": ("HRR", [(0, "DRAFT_READY")]),
}


def status_rank(tool: str, status: str) -> int:
    names = [s for _, s in PROGRESSIONS[tool][1]]
    return names.index(status) if status in names else -1


@dataclass
class MockBackends:
    clock: "SimClock"
    failures: dict = field(default_factory=dict)      # tool -> list of modes, or "down"
    records: dict = field(default_factory=dict)       # ref -> record
    idempotency: dict = field(default_factory=dict)   # key -> response
    reported_plates: set = field(default_factory=set)
    call_log: list = field(default_factory=list)
    registry: dict = field(default_factory=dict)       # mock government records: nid -> citizen record
    _seq: itertools.count = field(default_factory=lambda: itertools.count(100001))

    def inject(self, tool: str, modes):
        self.failures[tool] = modes if modes == "down" else list(modes)

    def clear_failures(self):
        self.failures.clear()

    def _maybe_fail(self, tool: str):
        f = self.failures.get(tool)
        if f == "down":
            raise ToolUnavailable(f"{tool} returned 503")
        if f:
            mode = f.pop(0)
            if mode == "timeout":
                raise ToolTimeout(f"{tool} timed out after 8s")
            if mode == "error":
                raise ToolUnavailable(f"{tool} returned 502")
            if mode.startswith("reject:"):
                raise ToolRejected(mode.split(":", 1)[1])

    def call(self, tool: str, args: dict, idem_key: str) -> dict:
        self.call_log.append({"tool": tool, "idem": idem_key, "t": self.clock.now})
        if tool == "identity.verify_identity":
            rec = self.registry.get(args["nid"])
            return {"verified": bool(rec and rec.get("identity_verified")), "source": "mock identity service"}
        if tool == "vehicle.get_user_vehicles":
            rec = self.registry.get(args["nid"]) or {}
            return {"vehicles": [v["plate"] for v in rec.get("vehicles", [])]}
        if tool == "vehicle.verify_vehicle":
            rec = self.registry.get(args["nid"]) or {}
            return {"owner_match": any(v["plate"] == args["plate"] for v in rec.get("vehicles", [])),
                    "reported_stolen": args["plate"] in self.reported_plates}
        if tool == "commerce.verify_registration":
            rec = self.registry.get(args["nid"]) or {}
            return {"owner_match": any(c["cr"] == args["cr"] for c in rec.get("commercial_registrations", []))}
        if tool == "traffic.check_licence_status":
            lic = (self.registry.get(args["nid"]) or {}).get("licence", {})
            return {"licence_status": lic.get("status"), "expires": lic.get("expires"), "photo_year": lic.get("photo_year")}
        if tool == "traffic.find_branch_slot":
            return {"branch": "Olaya service branch (mock)", "slot": "Sun 2 Nov 2026, 10:30"}
        if tool == "hr.verify_employee":
            return {"eligible_to_contract": True, "source": "mock labour registry"}
        if tool == "hr.check_existing_contract":
            return {"existing_contract": False}
        if tool == "case.get_status":
            rec = self.records.get(args["ref"])
            if not rec:
                raise ToolRejected("unknown reference")
            return {"ref": rec["ref"], "status": rec["status"], "history": rec["history"]}
        self._maybe_fail(tool)
        if idem_key in self.idempotency:                       # duplicate-safe
            return dict(self.idempotency[idem_key], duplicate=True)
        if tool == "police.file_theft_report" and args["plate"] in self.reported_plates:
            raise ToolRejected("vehicle already has an open theft report")
        prefix, prog = PROGRESSIONS[tool]
        ref = f"{prefix}-{next(self._seq)}"
        rec = {"ref": ref, "tool": tool, "created": self.clock.now, "status": prog[0][1],
               "history": [{"t": self.clock.now, "status": prog[0][1]}]}
        self.records[ref] = rec
        if tool == "police.file_theft_report":
            self.reported_plates.add(args["plate"])
        resp = {"ref": ref, "status": rec["status"]}
        self.idempotency[idem_key] = resp
        return dict(resp)

    def advance(self):
        for rec in self.records.values():
            _, prog = PROGRESSIONS[rec["tool"]]
            elapsed = self.clock.now - rec["created"]
            target = [s for t, s in prog if elapsed >= t][-1]
            if target != rec["status"]:
                rec["status"] = target
                rec["history"].append({"t": self.clock.now, "status": target})

    def submissions(self, tool: str) -> int:
        return sum(1 for r in self.records.values() if r["tool"] == tool)


@dataclass
class SimClock:
    now: float = 0.0   # minutes

    def advance(self, minutes: float):
        self.now += minutes


@dataclass
class Breaker:
    failures: int = 0
    open_until: float = -1.0


class ResilientClient:
    MAX_ATTEMPTS = 3
    BACKOFF_S = [0, 2, 4]
    BREAKER_THRESHOLD = 3
    BREAKER_COOLDOWN_MIN = 30

    def __init__(self, backends: MockBackends, clock: SimClock):
        self.backends, self.clock = backends, clock
        self.breakers: dict[str, Breaker] = {}
        self.authz = None      # deterministic authorization hook, set by the orchestrator from the authenticated session

    def _breaker(self, tool: str) -> Breaker:
        return self.breakers.setdefault(tool.split(".")[0], Breaker())

    def call(self, tool: str, args: dict, idem_key: str, journey_tools: set[str], on_event=None) -> dict:
        check_tool_call(tool, args, journey_tools)               # raises GuardrailViolation
        if self.authz is not None:                               # enforced again inside the gateway itself
            ok, code, why = self.authz(tool, args)
            if not ok:
                raise GuardrailViolation(code, why)
        br = self._breaker(tool)
        if br.open_until > self.clock.now:
            raise CircuitOpen(f"circuit open for {tool.split('.')[0]} until t+{br.open_until:.0f}m")
        last = None
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                res = self.backends.call(tool, args, idem_key)
                br.failures = 0
                if on_event:
                    on_event("attempt_ok", {"tool": tool, "attempt": attempt, "idempotency_key": idem_key})
                return res
            except ToolRejected:
                raise
            except (ToolTimeout, ToolUnavailable) as e:
                last = e
                br.failures += 1
                if on_event:
                    on_event("attempt_failed", {"tool": tool, "attempt": attempt, "error": str(e),
                                                "next_backoff_s": self.BACKOFF_S[attempt] if attempt < self.MAX_ATTEMPTS else None,
                                                "idempotency_key": idem_key})
                if br.failures >= self.BREAKER_THRESHOLD:
                    br.open_until = self.clock.now + self.BREAKER_COOLDOWN_MIN
                    raise CircuitOpen(f"{e}; circuit opened for {tool.split('.')[0]}") from e
        raise ToolUnavailable(str(last))
