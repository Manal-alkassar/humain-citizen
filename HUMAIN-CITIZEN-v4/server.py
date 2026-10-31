"""Zero-dependency demo server: python3 server.py  ->  http://localhost:8000"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
import secrets
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from humain import Agent
from humain.guardrails import mask_id
from humain.llm import LLMAdapter

WEB = Path(__file__).parent / "web"
SESSIONS: dict[str, Agent] = {}


def persona_card(a: Agent) -> dict:
    c = a.ctx
    return {"name": c["name"], "national_id": mask_id(c["national_id"]), "identity_verified": c["identity_verified"],
            "vehicles": [f"{v['make']} {v['model']} · {v['plate']}" for v in c["vehicles"]],
            "licence": f"{c['licence']['status']} (photo {c['licence']['photo_year']})",
            "businesses": [b["name"] for b in c["commercial_registrations"]], "persona": a.persona}


def guard_stats(a: Agent) -> dict:
    kinds = [e["kind"] for e in a.audit.entries]
    titles = [e["data"].get("title", "") for e in a.audit.entries]
    return {"injections_blocked": sum(1 for t in titles if "injection" in t),
            "gateway_rejections": sum(1 for t in titles if "rejected by gateway" in t),
            "consent_gates": sum(1 for t in titles if t.startswith("Explicit consent")),
            "step_up": sum(1 for t in titles if "Step-up authentication passed" in t),
            "escalations": kinds.count("ESCALATE"), "audit_entries": len(a.audit.entries),
            "audit_chain_valid": a.audit.verify(),
            "policy_date": a.policies.today, "failures": {k: v for k, v in a.backends.failures.items()},
            "breakers_open": [k for k, b in a.client.breakers.items() if b.open_until > a.clock.now]}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kw):
        super().__init__(*args, directory=str(WEB), **kw)

    def end_headers(self):
        # never let the browser show a cached old version of the app
        self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter console
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("api %s\n" % (args[0],))

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/capabilities":
            return self._json({"server_stt": bool(os.environ.get("HUMAIN_STT_URL")),
                               "llm": bool(os.environ.get("HUMAIN_LLM_URL"))})
        if u.path == "/api/audit":
            a = SESSIONS.get(parse_qs(u.query).get("sid", [""])[0])
            return self._json(a.audit.entries if a else [], 200 if a else 404)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if u.path == "/api/stt":
            return self._stt(self.rfile.read(n), self.headers.get("Content-Type", "audio/webm"))
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        if u.path == "/api/session":
            persona = data.get("persona", "omar")
            sid = secrets.token_hex(8)
            a = Agent(persona)
            a.llm = LLMAdapter.from_env()
            SESSIONS[sid] = a
            return self._json({"sid": sid, "persona": persona_card(a), "guard": guard_stats(a)})
        a = SESSIONS.get(data.get("sid", ""))
        if not a:
            return self._json({"error": "unknown session"}, 404)
        if u.path == "/api/message":
            text = str(data.get("text", ""))[:1000]
            channel = "voice" if data.get("channel") == "voice" else "text"
            conf = data.get("confidence")
            conf = float(conf) if isinstance(conf, (int, float)) else None
            r = a.handle(text, channel=channel, stt_confidence=conf)     # voice and text: same pipeline
        elif u.path == "/api/language":
            r = a.set_language(str(data.get("lang", "")))
        elif u.path == "/api/tick":
            r = a.tick(float(data.get("minutes", 15)))
        elif u.path == "/api/config":
            a.configure(inject=data.get("inject"), clear=bool(data.get("clear")), policy_date=data.get("policy_date"))
            r = a._result("")
        else:
            return self._json({"error": "not found"}, 404)
        r["guard"] = guard_stats(a)
        r["otp"] = a.otp        # demo only: shown on screen in place of an SMS
        return self._json(r)


    def _stt(self, audio: bytes, ctype: str):
        """Optional server-side speech-to-text (e.g. a self-hosted Whisper with an OpenAI-compatible API).
        Only the transcript comes back; the browser then sends it through /api/message like typed text."""
        url = os.environ.get("HUMAIN_STT_URL")
        if not url:
            return self._json({"error": "server STT not configured"}, 501)
        if len(audio) < 2000:
            return self._json({"error": "empty recording"}, 400)
        boundary = uuid.uuid4().hex
        model = os.environ.get("HUMAIN_STT_MODEL", "whisper-1")
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.webm\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + audio + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url.rstrip("/") + "/v1/audio/transcriptions", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read())
            return self._json({"text": out.get("text", "").strip(), "language": out.get("language")})
        except Exception as e:
            return self._json({"error": f"speech-to-text failed: {e.__class__.__name__}"}, 502)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8000))
    srv = None
    for p in range(port, port + 20):  # if an older copy is still running on 8000, use the next free port
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        sys.exit("No free port found between %d and %d" % (port, port + 19))
    url = f"http://localhost:{port}"
    print(f"HUMAIN CITIZEN demo running → {url}   (Ctrl+C to stop)")
    print(f"Serving files from: {WEB}")
    if os.environ.get("NO_BROWSER") != "1":
        import threading
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    srv.serve_forever()
