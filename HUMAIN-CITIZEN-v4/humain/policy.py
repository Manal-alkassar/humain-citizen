"""Versioned policy / service-knowledge layer (the source of truth, not the LLM)."""
from __future__ import annotations

import json
from pathlib import Path

POLICY_DIR = Path(__file__).parent / "policies"


def load_policies(folder: Path = POLICY_DIR) -> list[dict]:
    """Load every policy file in humain/policies/*.json (the knowledge base / structured RAG source)."""
    out = []
    for f in sorted(Path(folder).glob("*.json")):
        doc = json.loads(f.read_text(encoding="utf-8"))
        for p in doc["policies"]:
            out.append({**p, "file": f.name})
    return out


class PolicyStore:
    def __init__(self, folder: Path = POLICY_DIR):
        self.by_intent = {p["intent"]: p for p in load_policies(folder)}
        self.pinned: dict[str, str] = {}       # intent -> forced version (demo / tests)
        self.today = "2026-10-31"

    def set_date(self, iso: str):
        self.today = iso

    def pin(self, intent: str, version: str | None):
        if version is None:
            self.pinned.pop(intent, None)
        else:
            self.pinned[intent] = version

    def retrieve(self, intent: str) -> dict | None:
        """Return the policy version in force today (or pinned), with provenance."""
        p = self.by_intent.get(intent)
        if not p:
            return None
        versions = sorted(p["versions"], key=lambda v: v["effective"])
        if intent in self.pinned:
            v = next(v for v in versions if v["version"] == self.pinned[intent])
        else:
            in_force = [v for v in versions if v["effective"] <= self.today]
            v = in_force[-1] if in_force else versions[0]
        return {"policy_id": p["id"], "file": p["file"], "title": p["title"], "authority": p["authority"], "action": p["action"], **v,
                "retrieved_on": self.today}


# ---------------------------------------------------------------- eligibility rules
def _sel_vehicle(ctx, slots):
    return slots.get("vehicle")


RULES = {
    "identity_verified": (lambda ctx, s, be: ctx["identity_verified"],
                           {"en": "Identity must be verified", "ar": "يجب التحقق من الهوية"}),
    "owns_selected_vehicle": (lambda ctx, s, be: s.get("vehicle") in [v["plate"] for v in ctx["vehicles"]],
                               {"en": "Requester must be the registered owner", "ar": "يجب أن يكون مقدم الطلب هو المالك المسجل"}),
    "vehicle_not_already_reported": (lambda ctx, s, be: s.get("vehicle") not in be.reported_plates,
                                     {"en": "No open theft report for this vehicle", "ar": "لا يوجد بلاغ سرقة مفتوح لهذه المركبة"}),
    "licence_valid_or_recent": (lambda ctx, s, be: ctx["licence"]["status"] == "valid" or
                                 (ctx["licence"]["status"] == "expired" and ctx["licence"]["expires"] >= "2025-10-31"),
                                 {"en": "Licence must be valid or expired < 1 year (not suspended)",
                                  "ar": "يجب أن تكون الرخصة سارية أو منتهية منذ أقل من سنة (غير موقوفة)"}),
    "owns_selected_cr": (lambda ctx, s, be: s.get("cr") in [c["cr"] for c in ctx["commercial_registrations"]],
                          {"en": "Requester must own the commercial registration", "ar": "يجب أن يكون مقدم الطلب مالك السجل"}),
    "cr_no_active_employees": (lambda ctx, s, be: _cr(ctx, s).get("active_employees", 1) == 0,
                                {"en": "No active employees on the registration", "ar": "لا يوجد موظفون نشطون على السجل"}),
    "cr_no_outstanding_obligations": (lambda ctx, s, be: not _cr(ctx, s).get("outstanding_obligations", ["?"]),
                                       {"en": "No outstanding obligations (e.g. zakat filing)", "ar": "لا توجد التزامات قائمة (مثل إقرار الزكاة)"}),
    "owns_any_cr": (lambda ctx, s, be: bool(ctx["commercial_registrations"]),
                     {"en": "Requester must own a business registration", "ar": "يجب امتلاك سجل تجاري"}),
    "has_employer": (lambda ctx, s, be: bool(ctx.get("employer")),
                      {"en": "Requester must have a registered employer", "ar": "يجب وجود صاحب عمل مسجل"}),
}


def _cr(ctx, s):
    return next((c for c in ctx["commercial_registrations"] if c["cr"] == s.get("cr")), {})


CONDITIONS = {
    "vehicle_insured": lambda ctx, s: any(v["plate"] == s.get("vehicle") and v.get("insured") for v in ctx["vehicles"]),
    "photo_older_than_5y": lambda ctx, s: int(ctx["licence"]["photo_year"]) <= 2021,
}
