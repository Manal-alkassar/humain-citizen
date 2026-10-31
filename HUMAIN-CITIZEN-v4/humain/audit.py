"""Append-only, hash-chained audit trail with PII redaction at write time."""
from __future__ import annotations

import hashlib
import json

from .guardrails import redact


class AuditLog:
    def __init__(self):
        self.entries: list[dict] = []

    def append(self, t: float, kind: str, data: dict) -> dict:
        prev = self.entries[-1]["hash"] if self.entries else "GENESIS"
        body = {"seq": len(self.entries), "t": round(t, 2), "kind": kind, "data": redact(data), "prev": prev}
        body["hash"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
        self.entries.append(body)
        return body

    def verify(self) -> bool:
        prev = "GENESIS"
        for e in self.entries:
            body = {k: v for k, v in e.items() if k != "hash"}
            if body["prev"] != prev:
                return False
            h = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
            if h != e["hash"]:
                return False
            prev = e["hash"]
        return True

    def dump(self) -> str:
        return json.dumps(self.entries, ensure_ascii=False)
