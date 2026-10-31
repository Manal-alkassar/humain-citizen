"""HUMAIN CITIZEN orchestrator.

Understand -> Retrieve policy -> Plan -> Ask -> Verify -> Act -> Monitor
-> Adapt -> Notify -> Verify outcome.

The orchestrator owns state, permissions and tool execution. The language
layer (deterministic NLU or an optional LLM) only proposes an intent; the
versioned policy layer decides requirements; the guarded gateway executes.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import nlu
from .audit import AuditLog
from .gateway import (CircuitOpen, MockBackends, ResilientClient, SimClock, ToolRejected, ToolUnavailable,
                      status_rank)
from .guardrails import (GuardrailViolation, authorize, issue_consent, mask_id, plan_hash, redact, screen_input,
                         verify_consent)
from . import security_agent
from .messages import t
from .policy import CONDITIONS, RULES, PolicyStore

DATA = Path(__file__).parent / "data"
PERSONAS = json.loads((DATA / "personas.json").read_text(encoding="utf-8"))["personas"]

MEANINGFUL = {"ACCEPTED_BY_EMPLOYEE", "ACTIVE", "REGISTERED", "FLAGGED", "UNDER_INVESTIGATION", "ACKNOWLEDGED", "APPROVED", "ISSUED", "DISPATCHED",
              "UNDER_REVIEW", "CANCELLED", "ASSIGNED"}

STATUS_TEXT = {
    "RECEIVED": "received", "REGISTERED": "report registered", "UNDER_INVESTIGATION": "under investigation",
    "PENDING": "pending", "FLAGGED": "vehicle flagged — transfer blocked, checkpoints alerted",
    "SENT": "sent", "ACKNOWLEDGED": "acknowledged by insurer", "PAID": "paid", "SUBMITTED": "submitted",
    "APPROVED": "approved", "ISSUED": "licence issued", "DISPATCHED": "dispatched for delivery",
    "UNDER_REVIEW": "under review", "CANCELLED": "registration cancelled", "DRAFT_READY": "draft ready",
    "QUEUED": "queued for officer", "ASSIGNED": "assigned to a labour officer",
    "SENT_TO_EMPLOYEE": "sent to employee for approval", "ACCEPTED_BY_EMPLOYEE": "employee accepted the contract", "ACTIVE": "contract active",
}


STATUS_TEXT_AR = {
    "RECEIVED": "تم الاستلام", "REGISTERED": "تم تسجيل البلاغ", "UNDER_INVESTIGATION": "قيد التحقيق",
    "PENDING": "قيد الانتظار", "FLAGGED": "تم تعميم المركبة — منع نقل الملكية وتنبيه نقاط التفتيش",
    "SENT": "تم الإرسال", "ACKNOWLEDGED": "استلمته شركة التأمين", "PAID": "تم السداد", "SUBMITTED": "تم التقديم",
    "APPROVED": "تمت الموافقة", "ISSUED": "تم إصدار الرخصة", "DISPATCHED": "خرجت للتوصيل",
    "UNDER_REVIEW": "قيد المراجعة", "CANCELLED": "تم شطب السجل", "DRAFT_READY": "المسودة جاهزة",
    "QUEUED": "بانتظار الموظف", "ASSIGNED": "أُسندت إلى موظف مختص",
    "SENT_TO_EMPLOYEE": "أُرسل للموظف للموافقة", "ACCEPTED_BY_EMPLOYEE": "وافق الموظف على العقد", "ACTIVE": "العقد ساري",
}
DETAIL_AR = {"appointment": "الموعد", "vehicle": "المركبة", "incident time": "وقت الحادثة", "incident location": "الموقع", "loss reason": "السبب",
             "delivery method": "طريقة الاستلام", "photo update": "تحديث الصورة", "cr": "السجل", "closure reason": "سبب الشطب",
             "job title": "المسمى", "monthly salary": "الراتب", "employee": "الموظف", "termination reason": "السبب",
             "last working day": "آخر يوم عمل"}
VALUE_AR = {"lost": "مفقودة", "stolen": "مسروقة", "damaged": "تالفة", "pickup": "استلام من الفرع", "delivery": "توصيل",
            "confirmed": "موافق"}


FIELD_LABEL = {"vehicle": ("the vehicle details", "بيانات السيارة"), "incident_time": ("when you last saw it", "وقت آخر مرة شفتها"),
               "incident_location": ("where you last saw it", "مكان آخر مرة شفتها"), "loss_reason": ("the reason", "السبب"),
               "delivery_method": ("how you'd like to receive it", "طريقة الاستلام"), "cr": ("the business", "السجل"),
               "closure_reason": ("the reason", "السبب"), "employee": ("the employee", "اسم الموظف"), "job_title": ("the job title", "المسمى"),
               "monthly_salary": ("the salary", "الراتب"), "termination_reason": ("the reason", "السبب"),
               "last_working_day": ("your last day", "آخر يوم دوام"), "photo_update": ("the photo confirmation", "تأكيد الصورة")}
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "couple of": 2}


def last_seen_date(text: str | None, today: str) -> str | None:
    """Turn 'last night' / 'قبل يومين' / '3 days ago' into a calendar date (relative to the policy clock)."""
    import datetime
    if not text:
        return None
    t = nlu.normalise(text)
    d = datetime.date.fromisoformat(today)
    back = None
    if re.search(r"today|this morning|tonight|اليوم|الصبح|الفجر|الليله|ساع|hour|minute|دقيق", t):
        back = 0
    if re.search(r"last night|yesterday|امس|البارحه", t):
        back = 1
    if re.search(r"يومين", t):
        back = 2
    m = re.search(r"\b(\d+|a|an|one|two|three|four|five|couple of)\s+days?\s+ago", t) or re.search(r"قبل\s+(\d+)\s+ايام", t)
    if m:
        back = int(m.group(1)) if m.group(1).isdigit() else _NUM.get(m.group(1), None)
    if re.search(r"اسبوعين", t) or re.search(r"two weeks", t):
        back = 14
    elif re.search(r"اسبوع|a week ago|one week", t):
        back = 7
    return None if back is None else (d - datetime.timedelta(days=back)).isoformat()


def status_text(st: str, lang: str) -> str:
    return (STATUS_TEXT_AR if lang == "ar" else STATUS_TEXT).get(st, st)


@dataclass
class Journey:
    intent: str
    policy: dict
    case_id: str
    lang: str = "en"
    slots: dict = field(default_factory=dict)
    plan: list = field(default_factory=list)
    plan_hash: str = ""
    consent: str | None = None
    refs: dict = field(default_factory=dict)          # tool -> ref
    last_status: dict = field(default_factory=dict)   # ref -> status
    pending: list = field(default_factory=list)       # steps awaiting recovery
    status: str = "collecting"
    escalation: str | None = None
    outcome_verified: bool = False
    reask: int = 0
    notified: set = field(default_factory=set)
    flags: dict = field(default_factory=dict)        # identity_verified, ownership_verified, security decision…


class Agent:
    def __init__(self, persona: str = "omar", policies: PolicyStore | None = None, seed: int = 7):
        self.ctx = copy.deepcopy(PERSONAS[persona])
        self.persona = persona
        self.policies = policies or PolicyStore()
        self.clock = SimClock()
        # Authenticated identity comes from the host app's login (mock SSO): never from chat text or a frontend field.
        self.session = {"nid": self.ctx["national_id"], "source": "host-app SSO (mock)"}
        self.backends = MockBackends(self.clock, registry={self.ctx["national_id"]: self.ctx})
        self.client = ResilientClient(self.backends, self.clock)
        self.client.authz = lambda tool, args: authorize(tool, args, self.session["nid"], self.ctx)
        self.audit = AuditLog()
        self.rng = random.Random(seed)
        self.mode = "idle"            # idle | collecting | confirm | stepup | offer_next
        self.active: Journey | None = None
        self.journeys: list[Journey] = []
        self.queue: list[str] = []
        self.notifications: list[dict] = []
        self.trace: list[dict] = []
        self.otp: str | None = None
        self.otp_attempts = 0
        self.unsafe_actions = 0       # must stay 0 — counted by tests
        self._ticket_seq = itertools.count(40001)
        self._case_seq = itertools.count(4821)
        self.llm = None               # optional humain.llm.LLMAdapter
        self.lang: str | None = None  # conversation language: set on first message, changed only by the selector
        self.last_detect: dict = {}
        self.voice_min_confidence = 0.55

    # ------------------------------------------------------------------ helpers
    def _emit(self, kind: str, title: str, detail: dict | None = None):
        ev = {"t": round(self.clock.now, 1), "type": kind, "title": title, "detail": redact(detail or {})}
        self.trace.append(ev)
        self.audit.append(self.clock.now, kind, {"title": title, **(detail or {})})
        return ev

    def _notify(self, journey: Journey | None, key: str, text: str, level: str = "info"):
        dedup = (journey.case_id if journey else "-", key)
        if journey is not None:
            if dedup in journey.notified:
                return
            journey.notified.add(dedup)
        n = {"t": round(self.clock.now, 1), "case": journey.case_id if journey else None, "text": text, "level": level}
        self.notifications.append(n)
        self._emit("NOTIFY", "Push notification sent", n)

    def _ticket(self) -> str:
        return f"ESC-{next(self._ticket_seq)}"

    def _result(self, reply: str, quick: list[str] | None = None) -> dict:
        trace, self.trace = self.trace, []
        return {"reply": reply, "trace": trace, "mode": self.mode, "quick_replies": quick or [],
                "lang": self.lang or "en", "detected": self.last_detect, "journey": self._journey_summary(),
                "state": self.workflow_state(),
                "cases": self.cases_summary(), "notifications": self.notifications[-10:], "clock": self.clock.now}

    def workflow_state(self):
        """Structured workflow state of the current journey (what the Planning Agent knows)."""
        j = self.active or (self.journeys[-1] if self.journeys else None)
        if j is None:
            return None
        st = {"intent": j.intent, "policy": f"{j.policy['file']} v{j.policy['version']}"}
        if j.intent == "report_stolen_vehicle":
            veh = next((v for v in self.ctx["vehicles"] if v["plate"] == j.slots.get("vehicle")), None)
            st.update({"vehicle": f"{veh['make']} {veh['model']}" if veh else None, "vehicle_make": veh["make"] if veh else None,
                       "vehicle_model": veh["model"] if veh else None, "vehicle_plate": veh["plate"] if veh else None,
                       "last_seen_time": j.slots.get("incident_time"),
                       "last_seen_date": last_seen_date(j.slots.get("incident_time"), self.policies.today),
                       "last_seen_location": j.slots.get("incident_location")})
        else:
            st.update({k: v for k, v in j.slots.items()})
        st.update({"identity_verified": j.flags.get("identity_verified", False),
                   "ownership_verified": j.flags.get("ownership_verified", None if j.intent in ("leave_job",) else False),
                   "required_information_complete": not self._missing(j),
                   "security_decision": j.flags.get("security"),
                   "citizen_confirmation": "given" if j.consent else ("requested" if j.status == "awaiting_consent" else "not yet"),
                   "submission_status": j.status, "case_id": j.case_id})
        return st

    def _journey_summary(self):
        j = self.active or (self.journeys[-1] if self.journeys else None)
        return None if j is None else {"intent": j.intent, "case": j.case_id, "policy_id": j.policy["policy_id"], "status": j.status}

    # ------------------------------------------------------------------ public API
    def handle(self, text: str, channel: str = "text", stt_confidence: float | None = None) -> dict:
        """Single entry point for BOTH text and voice. Voice arrives here as a transcript, so it goes
        through exactly the same guardrails, policy checks, consent and gateway as typed text."""
        text = (text or "").strip()
        det = nlu.detect_language(text)
        self.last_detect = det
        lang = self.lang or det["lang"] or "en"
        if not text:
            return self._result(t("empty", lang))
        if channel == "voice" and stt_confidence is not None and 0 < stt_confidence < self.voice_min_confidence:
            self._emit("GUARD", "Low-confidence transcription — not acted on",
                       {"channel": "voice", "stt_confidence": stt_confidence, "action": "asked citizen to repeat"})
            return self._result(t("unclear_audio", lang))
        if not det["supported"]:
            self._emit("INPUT", "Unsupported language — asked to use Arabic or English", {"channel": channel})
            return self._result(t("unsupported_lang", lang))
        if self.lang is None and det["lang"]:
            self.lang = det["lang"]
            lang = self.lang
        self._emit("INPUT", "Citizen message", {"text": text, "channel": channel, "detected_language": det["lang"],
                                                "code_switching": det["mixed"], "conversation_language": lang,
                                                **({"stt_confidence": stt_confidence} if channel == "voice" else {})})
        hits = screen_input(text)
        if hits:
            self._emit("GUARD", "Prompt/tool-injection blocked", {"signatures": hits[:3], "action": "refused, no tool call"})
            return self._result(t("blocked", lang))

        if self.mode == "confirm":
            return self._on_confirm(text, lang)
        if self.mode == "stepup":
            return self._on_stepup(text, lang)
        if self.mode == "offer_next":
            yn = nlu.yes_no(text)
            nxt = self.queue.pop(0) if self.queue else None
            self.mode = "idle"
            if yn and nxt:
                return self._start(nxt, text, lang, carry_text=False)
            if yn is False:
                return self._result(t("cancelled", lang) if nxt else t("fallback", lang))
        if self.mode == "collecting" and self.active:
            res = nlu.classify(text, self._context_concepts(text))
            if nlu.yes_no(text) is False and len(text.split()) <= 3:
                return self._cancel(lang)
            switch = res.intent and res.intent not in (self.active.intent, "case_status") and res.confidence >= 0.7
            filled = [] if switch else self._fill_slots(self.active, text, collecting=True)
            if not filled and res.intent and res.intent != self.active.intent and res.confidence >= 0.6:
                self._emit("ADAPT", "Citizen changed topic — parking current journey", {"from": self.active.intent, "to": res.intent})
                self.active.status = "parked"
                self.mode = "idle"
                r = self._start(res.intent, text, lang)
                r["reply"] = t("switch", lang) + "\n\n" + r["reply"]
                return r
            if not filled:
                self.active.reask += 1
                if self.active.reask >= 3:
                    ticket = self._ticket()
                    self.active.escalation = ticket
                    self._emit("ESCALATE", "Repeated misunderstanding → human hand-off", {"ticket": ticket})
                    self.mode = "idle"
                    return self._result(t("handoff_offer", lang, ticket=ticket))
                q = self._question(self.active, self._missing(self.active), lang)
                self._emit("ASK", "Clarification requested", {"missing": self._missing(self.active)})
                return self._result(t("reask", lang, q=q))
            j = self.active
            still = self._missing(j)
            if still:
                labels = [FIELD_LABEL.get(f, (f, f))[1 if lang == "ar" else 0] for f in filled]
                ack = t("ack_partial", lang, fields=(" و".join(labels) if lang == "ar" else " and ".join(labels)))
            else:
                ack = t("ack_complete", lang)
            r = self._advance(j, lang)
            if r["mode"] in ("collecting", "confirm"):
                r["reply"] = ack + "\n\n" + r["reply"]
            return r

        if re.search(r"\b(who|what) are you\b|\bare you (a |an )?(bot|ai|robot|chatgpt|gpt|human)\b|\bwhat model\b|\bwho (made|built) you\b|من انت|مين انت|وش انت", text, re.I):
            self._emit("INTENT", "Identity question", {})
            return self._result(t("identity", lang))
        res = self._classify(text)
        if res.intent is None:
            if re.search(r"^\s*(hi|hello|hey|salam|السلام|مرحبا|هلا)\b", text, re.I):
                return self._result(t("greeting", lang, name=(self.ctx.get("name_ar") if lang == "ar" else None or self.ctx["name"]).split()[0]))
            self._emit("INTENT", "No supported intent (out of scope)", {"confidence": res.confidence, "engine": res.engine})
            return self._result(t("fallback", lang))
        if res.intent == "case_status":
            return self._status(lang, res)
        if res.intent == "report_stolen_vehicle" and "LICENCE" in res.concepts:
            if "replace_lost_licence" not in res.secondary:
                res.secondary.append("replace_lost_licence")  # licence was in the stolen car
            self._carry = {"replace_lost_licence": {"loss_reason": "stolen"}}
        for s in res.secondary:
            if s not in self.queue:
                self.queue.append(s)
        return self._start(res.intent, text, lang, res=res)

    def set_language(self, lang: str) -> dict:
        """Switch the conversation language mid-journey without touching case state."""
        if lang not in ("ar", "en"):
            return self._result("")
        old, self.lang = self.lang, lang
        self._emit("ADAPT", f"Conversation language switched {old or '-'} → {lang} (state preserved)",
                   {"mode": self.mode, "active_case": self.active.case_id if self.active else None})
        j = self.active
        if self.mode == "collecting" and j:
            missing = self._missing(j)
            return self._result(t("lang_switched", lang) + "\n\n" + self._question(j, missing, lang), self._quick(j, missing))
        if self.mode == "confirm" and j:
            reply, quick = self._confirm_message(j, lang)
            return self._result(t("lang_switched", lang) + "\n\n" + reply, quick)
        if self.mode == "stepup":
            return self._result(t("lang_switched", lang) + "\n\n" + t("stepup", lang, otp=self.otp), [self.otp])
        return self._result(t("lang_switched" if self.journeys else "lang_switched_idle", lang))

    def tick(self, minutes: float = 15) -> dict:
        """Advance simulated time: monitor cases, recover, notify, verify outcomes."""
        self.clock.advance(minutes)
        self.backends.advance()
        self._emit("MONITOR", f"Monitoring sweep at t+{self.clock.now:.0f} min", {"open_cases": len(self.journeys)})
        for j in self.journeys:
            if j.pending:
                self._resume(j)
            for tool, ref in list(j.refs.items()):
                try:
                    st = self.client.call("case.get_status", {"ref": ref}, f"status-{ref}-{self.clock.now}", set())
                except Exception as e:  # pragma: no cover - status tool is reliable in mock
                    self._emit("ADAPT", "Status check failed", {"ref": ref, "error": str(e)})
                    continue
                if st["status"] != j.last_status.get(ref):
                    old = j.last_status.get(ref)
                    j.last_status[ref] = st["status"]
                    self._emit("MONITOR", f"{ref}: {old} → {st['status']}", {"tool": tool, "ref": ref})
                    if st["status"] in MEANINGFUL:
                        lg = self.lang or "en"
                        self._notify(j, f"{ref}:{st['status']}",
                                     t("n_status", lg, title=j.policy["title"][lg], status=status_text(st["status"], lg), ref=ref))
            self._verify_outcome(j)
        return self._result("")

    def configure(self, *, inject: dict | None = None, clear: bool = False, policy_date: str | None = None,
                  pin: dict | None = None):
        if clear:
            self.backends.clear_failures()
            for b in self.client.breakers.values():
                b.failures, b.open_until = 0, -1
        for tool, modes in (inject or {}).items():
            self.backends.inject(tool, modes)
        if policy_date:
            self.policies.set_date(policy_date)
        for intent, ver in (pin or {}).items():
            self.policies.pin(intent, ver)

    # ------------------------------------------------------------------ understanding
    def _context_concepts(self, text: str) -> set[str]:
        tn = nlu.normalise(text)
        extra = set()
        if any(nlu.normalise(c["name"]) in tn for c in self.ctx["commercial_registrations"]):
            extra.add("BUSINESS")
        if any(v["model"].lower() in tn or v["plate"].lower() in tn for v in self.ctx["vehicles"]):
            extra.add("VEHICLE")
        return extra

    def _classify(self, text):
        res = nlu.classify(text, self._context_concepts(text))
        if self.llm is not None:
            proposal = self.llm.propose_intent(text, nlu.INTENTS)
            if proposal and proposal in nlu.INTENTS:          # closed-set validation
                res.intent, res.engine = proposal, f"llm:{self.llm.name} (validated)"
        return res

    def _start(self, intent: str, text: str, lang: str, res=None, carry_text: bool = True) -> dict:
        res = res or nlu.classify(text)
        self._emit("INTENT", f"Intent: {intent}", {"confidence": round(res.confidence, 2), "engine": res.engine,
                                                   "concepts": res.concepts, "also_detected": res.secondary})
        pol = self.policies.retrieve(intent)
        self._emit("POLICY", f"Policy retrieved: {pol['file']} · {pol['policy_id']} v{pol['version']}",
                   {"file": pol["file"], "source_id": pol["source_id"], "effective": pol["effective"], "authority": pol["authority"],
                    "risk": pol["risk"], "autonomy": pol["autonomy"], "summary": pol["summary"]})
        j = Journey(intent=intent, policy=pol, case_id=f"HC-2026-{next(self._case_seq):06d}", lang=lang)
        self.active = j
        self._emit("CONTEXT", "Authenticated context loaded (minimised)",
                   {"name": self.ctx["name"], "national_id": mask_id(self.ctx["national_id"])})
        idv = self._read_tool(j, "identity.verify_identity", {"nid": self.ctx["national_id"]})
        if not (idv and idv["verified"]):
            return self._ineligible(j, ["identity_verified"], lang)
        if intent == "report_stolen_vehicle":
            self._read_tool(j, "vehicle.get_user_vehicles", {"nid": self.ctx["national_id"]})
        if intent == "replace_lost_licence":
            self._read_tool(j, "traffic.check_licence_status", {"nid": self.ctx["national_id"]})
        if intent in ("hire_employee", "end_employee_contract"):
            crs = self.ctx["commercial_registrations"]
            if not crs:
                return self._ineligible(j, ["owns_any_cr"], lang)
            self._read_tool(j, "commerce.verify_registration", {"nid": self.ctx["national_id"], "cr": crs[0]["cr"]})
        for k, v in getattr(self, "_carry", {}).pop(intent, {}).items():
            j.slots[k] = v
            self._emit("CONTEXT", "Carried over from earlier message", {k: v})
        if carry_text:
            self._fill_slots(j, text, collecting=False)
        sad = intent in ("report_stolen_vehicle", "replace_lost_licence")
        act = pol["action"]
        head = t("can_do", lang, opener=act["opener"][lang], action=act["name"][lang], promise=act["promise"][lang],
                 pid=pol["policy_id"], ver=pol["version"])
        r = self._advance(j, lang)
        r["reply"] = head + "\n\n" + r["reply"]
        return r

    # ------------------------------------------------------------------ slots
    def _active_slots(self, j: Journey) -> list[dict]:
        return [s for s in j.policy["slots"] if not s.get("condition") or CONDITIONS[s["condition"]](self.ctx, j.slots)]

    def _missing(self, j: Journey) -> list[str]:
        return [s["name"] for s in self._active_slots(j) if s["required"] and not j.slots.get(s["name"])]

    def _options(self, slot: str) -> list[dict]:
        if slot == "vehicle":
            return self.ctx["vehicles"]
        if slot == "cr":
            return self.ctx["commercial_registrations"]
        return []

    def _fill_slots(self, j: Journey, text: str, collecting: bool) -> list[str]:
        filled = []
        missing = self._missing(j)
        for s in self._active_slots(j):
            name, typ = s["name"], s["type"]
            if j.slots.get(name):
                continue
            val = None
            if typ == "owned_vehicle":
                opts = self._options("vehicle")
                if len(opts) == 1:
                    val = opts[0]["plate"]
                    self._emit("CONTEXT", "Only one registered vehicle — auto-selected", {"plate": val})
                else:
                    i = nlu.match_option(text, opts, ["plate", "model", "make", "colour", "model_ar", "make_ar"])
                    val = opts[i]["plate"] if i is not None else None
            elif typ == "owned_cr":
                opts = self._options("cr")
                if len(opts) == 1:
                    val = opts[0]["cr"]
                elif opts:
                    i = nlu.match_option(text, opts, ["cr", "name"])
                    if i is None:
                        tn = nlu.normalise(text)
                        for k, o in enumerate(opts):
                            if any(w in tn for w in nlu.normalise(o["name"]).split() if len(w) > 4):
                                i = k
                                break
                    val = opts[i]["cr"] if i is not None else None
            elif typ == "time_expr":
                val = nlu.extract_time(text)
            elif typ == "location":
                val = nlu.extract_location(text)
                if not val and collecting and missing == [name] and len(text.split()) <= 8:
                    val = text.strip(" .")
            elif typ.startswith("enum:"):
                val = nlu.extract_enum(text, typ)
            elif typ == "confirm":
                if collecting and missing and missing[0] == name:
                    yn = nlu.yes_no(text)
                    val = None if yn is None else ("confirmed" if yn else "declined")
            elif typ == "money":
                val = nlu.extract_money(text) if collecting or re.search(r"\d", text) else None
            elif typ == "free_text" and collecting:
                val = self._free_text(name, text, missing)
            elif typ == "free_text" and name.endswith("_reason"):
                m = re.search(r"\b(?:because|since|as|due to)\b\s+(.{3,200})|(?:لأن|بسبب)\s*(.{3,200})", text, re.I)
                val = (m.group(1) or m.group(2)).strip(" .") if m else None
            if val:
                j.slots[name] = val
                filled.append(name)
        # context-aware short answer: "مول الرياض" right after we asked where → it's the location
        if (collecting and "incident_location" in missing and "incident_location" not in filled and len(text.split()) <= 6
                and nlu.yes_no(text) is None and not screen_input(text) and nlu.PLACE_RE.search(nlu.normalise(text))):
            place = re.split(r"\s*(?:،|,)\s*", text.strip(" ."))
            j.slots["incident_location"] = next((x for x in place if nlu.PLACE_RE.search(nlu.normalise(x))), text.strip(" ."))
            filled.append("incident_location")
        if filled:
            self._emit("SLOTS", "Information extracted (Planning Agent state updated)", {k: j.slots[k] for k in filled})
        return filled

    def _free_text(self, name: str, text: str, missing: list[str]) -> str | None:
        txt = text.strip(" .")
        if name not in missing:
            return None
        if name == "job_title":
            m = re.search(r"\b(?:as\s+(?:an?\s+)?|an?\s+)([a-z][a-z \-]{2,40}?)(?=\s+(?:for|at|with|on|paying|earning)\b|[,.]|$)", txt, re.I)
            if m:
                return m.group(1).strip()
            txt2 = re.sub(r"\d[\d,\.]*\s*(k|sar|riyals?|ريال)?", "", txt, flags=re.I).strip(" ,.")
            return txt2 if txt2 and missing[0] == name else None
        if name == "employee":
            m = re.match(r"\s*([A-Z][a-z]+(?: [A-Z][a-z]+)?)", txt)
            if m:
                return m.group(1)
            return txt.split(",")[0][:60] if missing[0] == name else None
        if name in ("termination_reason", "closure_reason"):
            if len(missing) > 1 and missing[0] != name:
                parts = re.split(r",|\bbecause\b|\bas\b|\bsince\b|لأن|بسبب", txt, maxsplit=1)
                return parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
            return txt[:200]
        return txt[:120] if missing[0] == name else None

    def _question(self, j: Journey, missing: list[str], lang: str) -> str:
        qs = []
        for name in missing:
            if name in ("vehicle", "cr"):
                opts = self._options(name)
                if name == "vehicle" and lang == "ar":
                    o = "".join(f"\n   {i+1}) {v.get('make_ar', v['make'])} {v.get('model_ar', v['model'])} {v['year']} · {v['plate']}"
                                for i, v in enumerate(opts))
                elif name == "vehicle":
                    o = "".join(f"\n   {i+1}) {v['colour'].title()} {v['make']} {v['model']} {v['year']} · {v['plate']}"
                                 for i, v in enumerate(opts))
                else:
                    o = "".join(f"\n   {i+1}) {c['name']} · CR {c['cr']}" for i, c in enumerate(opts))
                qs.append(t(f"slot.{name}", lang, options=o))
            elif name == "photo_update":
                qs.append(t("slot.photo_update", lang, ver=j.policy["version"], year=self.ctx["licence"]["photo_year"]))
            else:
                qs.append(t(f"slot.{name}", lang))
        return " ".join(qs) if len(qs) == 1 else "\n".join(f"• {q}" for q in qs)

    # ------------------------------------------------------------------ plan / verify
    def _advance(self, j: Journey, lang: str) -> dict:
        if (j.slots.get("vehicle") in self.backends.reported_plates and "vehicle_not_already_reported" in j.policy["eligibility"]):
            return self._ineligible(j, ["vehicle_not_already_reported"], lang)     # detect duplicates before asking more
        missing = self._missing(j)
        if missing:
            self.mode = "collecting"
            self._emit("ASK", "Asking only for missing information",
                       {"missing": missing, "already_known": list(j.slots) + ["identity", "contact", "address"]})
            q = self._question(j, missing, lang)
            return self._result((t("ask_intro", lang) + "\n" if len(missing) > 1 else "") + q, self._quick(j, missing))
        if j.slots.get("photo_update") == "declined":
            return self._ineligible(j, [], lang, custom="A current photo is required by policy v3.1.")
        if j.slots.get("vehicle"):
            self._read_tool(j, "vehicle.verify_vehicle", {"nid": self.ctx["national_id"], "plate": j.slots["vehicle"]})
        if j.slots.get("cr"):
            self._read_tool(j, "commerce.verify_registration", {"nid": self.ctx["national_id"], "cr": j.slots["cr"]})
        if j.intent == "replace_lost_licence" and j.slots.get("delivery_method") == "pickup" and not j.slots.get("appointment"):
            slot = self._read_tool(j, "traffic.find_branch_slot", {"nid": self.ctx["national_id"]})
            if slot:
                j.slots["appointment"] = f"{slot['slot']} · {slot['branch']}"
        if j.intent == "hire_employee" and j.slots.get("employee") and self.ctx["commercial_registrations"]:
            cr = self.ctx["commercial_registrations"][0]["cr"]
            self._read_tool(j, "hr.verify_employee", {"employer_cr": cr, "employee": j.slots["employee"]})
            self._read_tool(j, "hr.check_existing_contract", {"employer_cr": cr, "employee": j.slots["employee"]})
        fails = []
        checks = {}
        for rule in j.policy["eligibility"]:
            ok = bool(RULES[rule][0](self.ctx, j.slots, self.backends))
            checks[rule] = "pass" if ok else "FAIL"
            if not ok:
                fails.append(rule)
        self._emit("VERIFY", "Eligibility checked against policy rules", {"policy": f"{j.policy['policy_id']} v{j.policy['version']}", **checks})
        if fails:
            return self._ineligible(j, fails, lang)
        j.plan = self._build_plan(j)
        j.plan_hash = plan_hash(j.case_id, j.plan)
        self._emit("PLAN", f"Plan built: {len(j.plan)} step(s)",
                   {"steps": [f"{s['id']}: {s['tool']}" for s in j.plan], "autonomy": j.policy["autonomy"],
                    "plan_hash": j.plan_hash, "consent_required": True, "step_up": j.policy.get("step_up_auth", False)})
        # ---- Agent 2: Security Agent reviews the Planning Agent's proposed action
        guard_events = sum(1 for e in self.audit.entries if e["kind"] == "GUARD")
        rv = security_agent.review(j.plan, j.policy, j.intent, self.session, self.ctx, j.slots, self.backends, guard_events)
        j.flags["security"] = rv["decision"]
        self._emit("SECURITY", f"Security Agent: {rv['decision']} — {rv['reason']}",
                   {"risk": rv["risk"], "requires": rv["requires"],
                    **{c["check"]: c["result"] for c in rv["checks"]}})
        if rv["decision"] == "BLOCK" or (rv["decision"] == "ESCALATE" and j.policy["autonomy"] != "human_review"):
            self.mode = "idle"
            j.status = "blocked_by_security" if rv["decision"] == "BLOCK" else "escalated"
            j.escalation = self._ticket()
            self._emit("ESCALATE", "Stopped by Security Agent — no action taken", {"ticket": j.escalation, "decision": rv["decision"]})
            key = "security_block" if rv["decision"] == "BLOCK" else "security_escalate"
            return self._result(t(key, lang, reason=rv["reason"], ticket=j.escalation))
        self.mode = "confirm"
        j.status = "awaiting_consent"
        self._emit("CONSENT", "Human approval gate — waiting for explicit confirmation", {"plan_hash": j.plan_hash})
        reply, quick = self._confirm_message(j, lang)
        return self._result(reply, quick)

    def _read_tool(self, j: Journey, tool: str, args: dict):
        """Read-only verification through the same guarded gateway (allow-list + schema), logged as VERIFY."""
        try:
            res = self.client.call(tool, args, f"ro-{tool}-{j.case_id}", set())
        except Exception as e:
            self._emit("ADAPT", f"Verification tool {tool} failed", {"error": str(e)})
            return None
        if tool == "identity.verify_identity":
            j.flags["identity_verified"] = bool(res["verified"])
            title = "Identity verified" if res["verified"] else "Identity NOT verified"
        elif tool == "vehicle.get_user_vehicles":
            title = f"Registered vehicles retrieved ({len(res['vehicles'])})"
        elif tool == "vehicle.verify_vehicle":
            j.flags["ownership_verified"] = bool(res["owner_match"])
            title = "Vehicle ownership verified" if res["owner_match"] else "Vehicle ownership NOT verified"
        elif tool == "traffic.check_licence_status":
            title = f"Licence status checked: {res['licence_status']}"
        elif tool == "traffic.find_branch_slot":
            title = f"Appointment arranged: {res['slot']}"
        elif tool == "hr.verify_employee":
            title = "Employee verified"
        elif tool == "hr.check_existing_contract":
            title = "Existing contract checked: none found" if not res["existing_contract"] else "Existing contract found"
        else:
            j.flags["ownership_verified"] = bool(res["owner_match"])
            title = "Business ownership verified" if res["owner_match"] else "Business ownership NOT verified"
        self._emit("VERIFY", f"{title} — tool {tool} (mock)", {"tool": tool, **res})
        return res

    def _confirm_message(self, j: Journey, lang: str):
        steps = "\n".join(f"{i+1}. {s['label'][lang]}" for i, s in enumerate(j.plan))
        note = ""
        if j.policy.get("fee_sar"):
            note += t("note_fee", lang, fee=j.policy["fee_sar"])
        if j.policy["autonomy"] == "assisted":
            note += t("note_assisted", lang)
        if j.policy["autonomy"] == "human_review":
            note += t("note_human", lang)
        yes, no = j.policy["action"]["confirm"][lang], ("إلغاء" if lang == "ar" else "Cancel")
        return t("confirm", lang, steps=steps, details=self._details(j, lang), autonomy_note=note), [yes, no]

    def _details(self, j: Journey, lang: str = "en") -> str:
        parts = []
        for k, v in j.slots.items():
            if k == "vehicle":
                veh = next(x for x in self.ctx["vehicles"] if x["plate"] == v)
                v = (f"{veh.get('make_ar', veh['make'])} {veh.get('model_ar', veh['model'])} ({v})" if lang == "ar"
                     else f"{veh['colour']} {veh['make']} {veh['model']} ({v})")
            elif k == "cr":
                c = next(x for x in self.ctx["commercial_registrations"] if x["cr"] == v)
                v = f"{c['name']} (CR {v})"
            elif lang == "ar":
                v = VALUE_AR.get(v, v)
            label = k.replace("_", " ")
            parts.append(f"{DETAIL_AR.get(label, label) if lang == 'ar' else label}: {v}")
        return "؛ ".join(parts) if lang == "ar" else "; ".join(parts)

    def _quick(self, j: Journey, missing: list[str]) -> list[str]:
        m = missing[0]
        ar = (self.lang == "ar")
        if m == "vehicle":
            return [(f"ال{v.get('model_ar', v['model'])}" if ar else f"The {v['model']}") for v in self.ctx["vehicles"]]
        if m == "cr":
            return [c["name"] for c in self.ctx["commercial_registrations"]]
        if m == "loss_reason":
            return ["ضائعة", "مسروقة", "تالفة"] if ar else ["Lost", "Stolen", "Damaged"]
        if m == "delivery_method":
            return ["توصيل لعنواني", "استلام من الفرع"] if ar else ["Deliver to my address", "Pick up from branch"]
        if m == "photo_update":
            return ["نعم", "لا"] if ar else ["Yes", "No"]
        return []

    def _build_plan(self, j: Journey) -> list[dict]:
        c, s = self.ctx, j.slots
        plan = []
        for step in j.policy["steps"]:
            if step.get("condition") and not CONDITIONS[step["condition"]](c, s):
                continue
            tool = step["tool"]
            args = {
                "police.file_theft_report": lambda: {"owner_nid": c["national_id"], "plate": s["vehicle"],
                                                     "incident_time": s["incident_time"], "incident_location": s["incident_location"]},
                "registry.flag_vehicle": lambda: {"plate": s["vehicle"], "police_ref": "$s1.ref"},
                "insurer.notify_claim": lambda: {"plate": s["vehicle"], "police_ref": "$s1.ref",
                                                 "insurer": next(v for v in c["vehicles"] if v["plate"] == s["vehicle"])["insurer"]},
                "payment.charge_fee": lambda: {"payer_nid": c["national_id"], "amount_sar": str(j.policy["fee_sar"]),
                                               "purpose": "LICENCE_REPLACEMENT"},
                "traffic.issue_replacement": lambda: {"holder_nid": c["national_id"], "licence_no": c["licence"]["number"],
                                                      "reason": s["loss_reason"], "delivery": s["delivery_method"],
                                                      "payment_ref": "$s1.ref"},
                "commerce.cancel_cr": lambda: {"owner_nid": c["national_id"], "cr": s["cr"], "reason": s["closure_reason"]},
                "hr.create_contract": lambda: {"employer_cr": c["commercial_registrations"][0]["cr"], "employee": s["employee"],
                                               "job_title": s["job_title"], "salary": s["monthly_salary"]},
                "hr.route_to_officer": lambda: {"employer_nid": c["national_id"], "employee": s["employee"],
                                                "reason": s["termination_reason"]},
                "hr.draft_resignation": lambda: {"worker_nid": c["national_id"], "employer": c["employer"],
                                                 "last_day": s["last_working_day"]},
            }[tool]()
            plan.append({**step, "args": args})
        return plan

    def _ineligible(self, j: Journey, fails: list[str], lang: str, custom: str | None = None) -> dict:
        self.mode = "idle"
        j.status = "ineligible"
        if "vehicle_not_already_reported" in fails:
            existing = next((x.case_id for x in self.journeys if x.slots.get("vehicle") == j.slots.get("vehicle")), "—")
            self._emit("ADAPT", "Duplicate report prevented — existing case found", {"plate": j.slots.get("vehicle"), "existing_case": existing})
            return self._result(t("already_reported", lang, case=existing), ["تحقق من الحالة" if lang == "ar" else "Check status"])
        ticket = None
        if "identity_verified" in fails:
            ticket = self._ticket()
            nxt = t("ineligible_next_identity", lang, ticket=ticket)
        elif j.intent == "close_business":
            nxt = t("ineligible_next_cr", lang)
            j.slots.pop("cr", None)
            self.active = j
            self.mode = "collecting"
        else:
            ticket = self._ticket()
            nxt = t("ineligible_next_generic", lang, ticket=ticket)
        lines = "\n".join(f"✗ {RULES[f][1][lang]}" for f in fails) or (custom or "")
        self._emit("ESCALATE" if ticket else "ADAPT", "Not eligible — no action taken",
                   {"failed_rules": fails, "ticket": ticket})
        if ticket:
            j.escalation = ticket
        return self._result(t("ineligible", lang, fails=lines, next=nxt))

    # ------------------------------------------------------------------ consent / act
    def _on_confirm(self, text: str, lang: str) -> dict:
        j = self.active
        yn = nlu.yes_no(text)
        if yn is None:
            res = nlu.classify(text)
            if res.intent and res.intent != j.intent and res.confidence >= 0.6:
                j.status = "parked"
                self.mode = "idle"
                return self._start(res.intent, text, lang)
            return self._result(t("confirm_reask", lang))
        if not yn:
            return self._cancel(lang)
        # policy may have changed while the citizen was deciding
        current = self.policies.retrieve(j.intent)
        if current["version"] != j.policy["version"]:
            old = j.policy["version"]
            self._emit("ADAPT", f"Policy changed v{old} → v{current['version']} — re-planning",
                       {"source_id": current["source_id"], "effective": current["effective"]})
            j.policy = current
            r = self._advance(j, lang)
            r["reply"] = t("policy_changed", lang, old=old, new=current["version"]) + "\n\n" + r["reply"]
            return r
        j.consent = issue_consent(j.case_id, j.plan_hash)
        self._emit("CONSENT", "Explicit consent captured", {"bound_to_plan": j.plan_hash})
        if j.policy.get("step_up_auth"):
            self.otp = f"{self.rng.randint(0, 999999):06d}"
            self.otp_attempts = 0
            self.mode = "stepup"
            self._emit("AUTH", "Step-up authentication requested (high-risk action)", {"method": "OTP to registered phone (mock)"})
            return self._result(t("stepup", lang, otp=self.otp), [self.otp])
        return self._execute(j, lang)

    def _on_stepup(self, text: str, lang: str) -> dict:
        code = re.sub(r"\D", "", text)
        if nlu.yes_no(text) is False:
            return self._cancel(lang)
        if code == self.otp:
            self._emit("AUTH", "Step-up authentication passed", {})
            self.otp = None
            return self._execute(self.active, lang)
        self.otp_attempts += 1
        self._emit("AUTH", "Step-up code rejected", {"attempt": self.otp_attempts})
        if self.otp_attempts >= 3:
            self.mode = "idle"
            self.active.status = "cancelled"
            self.otp = None
            return self._result(t("stepup_locked", lang))
        return self._result(t("stepup_fail", lang))

    def _cancel(self, lang: str) -> dict:
        if self.active:
            self.active.status = "cancelled"
            self._emit("CANCEL", "Journey cancelled by citizen — nothing submitted", {"case": self.active.case_id})
        self.mode = "idle"
        self.active = None
        return self._result(t("cancelled", lang))

    def _idem(self, j: Journey, step: dict) -> str:
        return hashlib.sha256(f"{j.case_id}:{step['id']}:{j.plan_hash}".encode()).hexdigest()[:16]

    def _resolve(self, args: dict, j: Journey) -> dict:
        out = {}
        for k, v in args.items():
            if isinstance(v, str) and v.startswith("$"):
                sid = v[1:].split(".")[0]
                tool = next(s["tool"] for s in j.plan if s["id"] == sid)
                v = j.refs.get(tool)
            out[k] = v
        return out

    def _run_step(self, j: Journey, step: dict) -> str:
        """Returns 'ok' | 'rejected:<reason>' | 'unavailable' | 'blocked'."""
        if not verify_consent(j.consent, j.case_id, j.plan_hash):
            self._emit("GUARD", "Consent token invalid — action blocked", {"tool": step["tool"]})
            return "blocked"
        args = self._resolve(step["args"], j)
        ok, code, why = authorize(step["tool"], args, self.session["nid"], self.ctx)
        self._emit("AUTHZ", f"Deterministic authorization: {'ALLOW' if ok else 'DENY ' + code} — {why}",
                   {"tool": step["tool"], "session": mask_id(self.session["nid"]), "decision": "ALLOW" if ok else "DENY"})
        if not ok:
            return "blocked"
        idem = self._idem(j, step)
        tools = {s["tool"] for s in j.policy["steps"]}

        def on_event(kind, d):
            if kind == "attempt_failed":
                self._emit("ADAPT", f"{d['tool']} attempt {d['attempt']} failed — " +
                           (f"retrying in {d['next_backoff_s']}s with same idempotency key" if d["next_backoff_s"] else "giving up"),
                           d)
        try:
            res = self.client.call(step["tool"], args, idem, tools, on_event)
        except GuardrailViolation as e:
            self._emit("GUARD", f"Tool call rejected by gateway: {e.code}", {"tool": step["tool"], "detail": e.detail})
            return "blocked"
        except ToolRejected as e:
            self._emit("ADAPT", "Business rejection — not retried", {"tool": step["tool"], "reason": e.reason})
            return f"rejected:{e.reason}"
        except (CircuitOpen, ToolUnavailable) as e:
            self._emit("ADAPT", "Service unavailable — circuit breaker / safe fallback", {"tool": step["tool"], "error": str(e)})
            return "unavailable"
        j.refs[step["tool"]] = res["ref"]
        j.last_status[res["ref"]] = res["status"]
        self._emit("ACT", f"Authorised tool executed: {step['tool']} → {res['ref']}",
                   {"args": args, "status": res["status"], "idempotency_key": idem, "duplicate": res.get("duplicate", False),
                    "policy": f"{j.policy['policy_id']} v{j.policy['version']}", "plan_hash": j.plan_hash,
                    "reason": step["label"]["en"]})
        return "ok"

    def _execute(self, j: Journey, lang: str) -> dict:
        self.mode = "idle"
        j.status = "executing"
        if j not in self.journeys:
            self.journeys.append(j)
        results, issue = [], None
        for step in j.plan:
            if any(d in [p["id"] for p in j.pending] for d in step.get("depends_on", [])):
                j.pending.append(step)
                continue
            out = self._run_step(j, step)
            if out == "ok":
                ref = j.refs[step["tool"]]
                results.append(f"✓ {step['label'][lang]} — {ref}")
            elif out == "unavailable":
                j.pending.append(step)
                if not j.escalation:
                    j.escalation = self._ticket()
                    self._emit("ESCALATE", "Queued for auto-resume + officer notified", {"ticket": j.escalation, "step": step["tool"]})
                issue = t("recovery", lang, service=step["tool"].split(".")[0], ticket=j.escalation)
                self._notify(j, "recovery", t("n_recovery", lang, title=j.policy["title"][lang], ticket=j.escalation), "warn")
            elif out.startswith("rejected"):
                if step.get("optional"):
                    results.append(f"– {step['label'][lang]} skipped ({out.split(':',1)[1]})")
                    continue
                j.escalation = self._ticket()
                issue = t("rejected", lang, reason=out.split(":", 1)[1], ticket=j.escalation)
                j.status = "rejected"
                self._emit("ESCALATE", "Escalated after rejection", {"ticket": j.escalation})
                break
            else:
                issue = t("blocked", lang)
                j.status = "blocked"
                break
        if j.status == "executing":
            j.status = "pending_recovery" if j.pending else "tracking"
        self._emit("MONITOR", "Case under monitoring", {"case": j.case_id, "refs": j.refs, "pending": [p["tool"] for p in j.pending]})
        if results:
            self._notify(j, "submitted", t("n_submitted", lang, title=j.policy["title"][lang], case=j.case_id))
        self.active = None
        res_txt = "\n".join(results) or ("لم يُنفذ أي إجراء بعد." if lang == "ar" else "Nothing has been submitted yet.")
        reply = t("done", lang, results=res_txt, case=j.case_id) if not issue else \
            t("done_partial", lang, results=res_txt, case=j.case_id, issue=issue)
        quick = []
        if self.queue:
            nxt = self.queue[0]
            reply += "\n\n" + t("queued_next", lang, title=self.policies.retrieve(nxt)["title"][lang])
            self.mode = "offer_next"
            quick = ["Yes", "Not now"]
        return self._result(reply, quick)

    def _resume(self, j: Journey):
        still = []
        for step in j.pending:
            deps = step.get("depends_on", [])
            if any(next(s["tool"] for s in j.plan if s["id"] == d) not in j.refs for d in deps):
                still.append(step)
                continue
            out = self._run_step(j, step)
            if out == "ok":
                self._emit("ADAPT", f"Resumed: {step['tool']} completed (original idempotency key)", {"ref": j.refs[step["tool"]]})
                lg = self.lang or "en"
                self._notify(j, f"resumed:{step['id']}", t("n_resumed", lg, title=j.policy["title"][lg], step=step["label"][lg], ref=j.refs[step["tool"]]))
            elif out.startswith("rejected") and step.get("optional"):
                continue
            else:
                still.append(step)
        j.pending = still
        if not still and j.status == "pending_recovery":
            j.status = "tracking"

    def _verify_outcome(self, j: Journey):
        if j.outcome_verified or j.status not in ("tracking",):
            return
        target = j.policy["target_state"]
        ok = all(tool in j.refs and status_rank(tool, j.last_status.get(j.refs[tool], "")) >= status_rank(tool, st)
                 for tool, st in target.items())
        if ok:
            j.outcome_verified = True
            j.status = "completed"
            self._emit("OUTCOME", "Outcome verified against policy target state",
                       {"target": target, "observed": {tool: j.last_status[j.refs[tool]] for tool in target}})
            lg = self.lang or "en"
            self._notify(j, "completed", t("n_completed", lg, title=j.policy["title"][lg], case=j.case_id), "success")

    # ------------------------------------------------------------------ status
    def _status(self, lang: str, res) -> dict:
        self._emit("INTENT", "Intent: case_status", {"confidence": round(res.confidence, 2)})
        if not self.journeys:
            return self._result(t("status_none", lang))
        lines = []
        for j in self.journeys:
            sts = "، ".join(status_text(j.last_status.get(r, ""), lang) for r in j.refs.values()) if lang == "ar" else \
                ", ".join(status_text(j.last_status.get(r, ""), lang) for r in j.refs.values())
            lines.append(t("status_line", lang, title=j.policy["title"][lang], case=j.case_id,
                           status=(sts or j.status) + (" ✓" if j.outcome_verified else "")))
        return self._result("\n".join(lines))

    def cases_summary(self) -> list[dict]:
        return [{"case": j.case_id, "title": j.policy["title"]["en"], "title_ar": j.policy["title"]["ar"],
                 "status_labels": {r: {"en": status_text(j.last_status.get(r, ""), "en"), "ar": status_text(j.last_status.get(r, ""), "ar")}
                                   for r in j.refs.values()},
                 "intent": j.intent, "status": j.status,
                 "policy": f"{j.policy['policy_id']} v{j.policy['version']}", "refs": j.refs,
                 "statuses": {r: j.last_status.get(r) for r in j.refs.values()}, "escalation": j.escalation,
                 "outcome_verified": j.outcome_verified, "pending": [p["tool"] for p in j.pending]}
                for j in self.journeys]
