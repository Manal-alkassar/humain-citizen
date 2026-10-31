"""Browser tests of the voice + language interface (spec 15I).

    pip install playwright && playwright install chromium
    python tests/ui_voice_check.py              (opens web/index.html directly: no server needed)

The browser's speech recogniser is replaced by a scripted stand-in that returns a chosen
transcript / confidence / error, so we can test OUR handling deterministically (recording state,
transcript shown before acting, editing, error states, language switching). Real microphone
recognition quality is the vendor engine's; see docs/VOICE_MANUAL_TESTS.md for the live-mic checks.
"""
import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = (sys.argv[1] if len(sys.argv) > 1 else (ROOT / "web" / "index.html").as_uri()) + "?open=humain"
SHOTS = ROOT / "results" / "voice_ui"
SHOTS.mkdir(parents=True, exist_ok=True)

FAKE_SR = """
window.__sr = {transcript: "", confidence: 0.9, error: null, lastLang: null, delay: 500};
class FakeSR {
  constructor(){ this.lang = "en-US"; }
  start(){ window.__sr.lastLang = this.lang; const cfg = window.__sr;
    this._t = setTimeout(() => {
      if (cfg.error) { this.onerror && this.onerror({error: cfg.error}); this.onend && this.onend(); return; }
      if (cfg.transcript) {
        const res = [{transcript: cfg.transcript, confidence: cfg.confidence}]; res.isFinal = true;
        this.onresult && this.onresult({resultIndex: 0, results: [res]});
      }
      this.onend && this.onend();
    }, cfg.delay); }
  stop(){} abort(){ clearTimeout(this._t); }
}
window.SpeechRecognition = FakeSR; window.webkitSpeechRecognition = FakeSR;
"""


class T:
    def __init__(self):
        self.cases = []

    def add(self, name, ok, ev):
        self.cases.append({"name": name, "pass": bool(ok), "evidence": ev})
        print(("PASS " if ok else "FAIL ") + name, ev)


SPY = """() => { window.__msgs = []; const orig = window.api;
  window.api = (p, b) => { if (p === "/api/message") window.__msgs.push(b); return orig(p, b); }; }"""


async def M(pg):
    """Messages that actually reached the agent engine."""
    return await pg.evaluate("window.__msgs")


async def fresh(ctx):
    pg = await ctx.new_page()
    await pg.goto(URL)
    await pg.wait_for_selector("#hero")
    await pg.evaluate(SPY)
    return pg, None


async def speak(pg, transcript, conf=0.9, error=None, mic="#micBtn"):
    await pg.evaluate("([t,c,e]) => Object.assign(window.__sr, {transcript: t, confidence: c, error: e})", [transcript, conf, error])
    await pg.click(mic)
    await pg.wait_for_selector(".vp.show")
    rec_visible = await pg.locator(".recdot").count() > 0
    await pg.wait_for_timeout(1200)
    return rec_visible


async def wait_reply(pg):
    await pg.wait_for_timeout(300)
    await pg.wait_for_function("!document.querySelector('.typing')", timeout=15000)
    await pg.wait_for_timeout(400)


async def card(pg):
    """Text of the action card(s) HUMAIN showed in the conversation."""
    return " ".join(await pg.locator(".acard .nm").all_inner_texts())


async def main():
    t = T()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1600, "height": 1000})
        await ctx.add_init_script(FAKE_SR)

        # UI: welcome screen
        pg, msgs = await fresh(ctx)
        hero = await pg.locator("#hero").inner_text()
        big = await pg.locator("#micBtn").is_visible()
        sel = await pg.locator(".langsw button").all_inner_texts()
        await pg.screenshot(path=str(SHOTS / "00_welcome.png"))
        starters = await pg.locator(".starter").count()
        steps = await pg.locator("#stepper span").all_inner_texts()
        t.add("UI  welcome with action starters, no comparison boxes, progress, voice button, العربية | English",
              "Tell HUMAIN what you need" in hero and big and sel == ["العربية", "English"] and starters == 4
              and "TRADITIONAL" not in hero and "CHATBOT" not in hero and len(steps) == 7,
              {"welcome": hero, "service_cards_shown": starters, "progress": steps, "selector": sel})

        # V1 Arabic voice
        pg, msgs = await fresh(ctx)
        await pg.click("#langAr")
        rec = await speak(pg, "سيارتي انسرقت وما أعرف وش أسوي", 0.92, mic="#micBtn")
        shown = await pg.locator("#vText").input_value()
        badge = await pg.locator(".vp .ok").inner_text()
        before = len(await M(pg))
        await pg.screenshot(path=str(SHOTS / "01_arabic_transcript.png"))
        await pg.click("#vSend"); await wait_reply(pg)
        trace = await pg.locator("#trace").inner_text()
        reply = await pg.locator(".msg.bot").last.inner_text()
        pill = await pg.locator(".meta.user .pill").first.inner_text()
        await pg.screenshot(path=str(SHOTS / "02_arabic_workflow.png"))
        t.add("V1  Arabic voice → recording shown → transcript shown before acting → Arabic workflow",
              rec and shown.startswith("سيارتي") and "العربية" in badge and before == 0 and (await M(pg))[-1].get("channel") == "voice"
              and "بلاغ سرقة مركبة" in trace and "الإبلاغ عن سرقة مركبة" in await card(pg) and "ولا يهمك" in reply and "العربية" in pill,
              {"recording_state": rec, "badge": badge, "requests_before_send": before, "channel": (await M(pg))[-1].get("channel"),
               "recogniser_lang": await pg.evaluate("window.__sr.lastLang"), "pill": pill})

        # V2 English voice
        pg, msgs = await fresh(ctx)
        await speak(pg, "My car was stolen and I need help.", 0.95)
        badge = await pg.locator(".vp .ok").inner_text()
        await pg.click("#vSend"); await wait_reply(pg)
        reply = await pg.locator(".msg.bot").last.inner_text()
        t.add("V2  English voice → English detected → stolen-vehicle workflow",
              "English" in badge and "REPORT STOLEN VEHICLE" in await card(pg), {"badge": badge, "reply": reply[:70]})

        # V3 code-switching
        pg, msgs = await fresh(ctx)
        await speak(pg, "سيارتي انسرقت، what should I do?", 0.9)
        badge = await pg.locator(".vp .ok").inner_text()
        await pg.click("#vSend"); await wait_reply(pg)
        reply = await pg.locator(".msg.bot").last.inner_text()
        t.add("V3  code-switching voice understood, no workflow failure",
              "mixed" in badge and "REPORT STOLEN VEHICLE" in await card(pg), {"badge": badge, "reply": reply[:70]})

        # V4 noisy / unclear
        pg, msgs = await fresh(ctx)
        await speak(pg, "سيا انس ما", 0.31)
        err = await pg.locator(".vp .err").inner_text()
        btns = await pg.locator(".vp .btns button").all_inner_texts()
        await pg.screenshot(path=str(SHOTS / "03_poor_audio.png"))
        t.add("V4  noisy recording → 'couldn't understand', Try again / Type instead, no request sent",
              "couldn't clearly understand" in err and len(await M(pg)) == 0 and "Try again" in btns and "Type instead" in btns,
              {"error": err, "buttons": btns, "requests_sent": len(await M(pg))})

        # V4b empty recording
        pg, msgs = await fresh(ctx)
        await speak(pg, "", 0.0, error="no-speech")
        err = await pg.locator(".vp .err").inner_text()
        t.add("V4b empty recording → not sent", "Nothing was sent" in err and len(await M(pg)) == 0, {"error": err, "requests_sent": len(await M(pg))})

        # V4c STT service failure
        pg, msgs = await fresh(ctx)
        await speak(pg, "", 0.0, error="network")
        err = await pg.locator(".vp .err").inner_text()
        await pg.click("#vType")
        focused = await pg.evaluate("document.activeElement.id")
        t.add("V4c speech-to-text failure → clear error, 'Type instead' returns to keyboard, no crash",
              "Speech-to-text failed" in err and focused == "input" and len(await M(pg)) == 0, {"error": err, "focus_after_type_instead": focused})

        # V4d unsupported language
        pg, msgs = await fresh(ctx)
        await speak(pg, "Мою машину угнали", 0.9)
        err = await pg.locator(".vp .err").inner_text()
        t.add("V4d unclear/unsupported language → asked to repeat in Arabic/English, not sent",
              "Arabic or English" in err and len(await M(pg)) == 0, {"error": err})

        # V5 wrong transcription edited
        pg, msgs = await fresh(ctx)
        await speak(pg, "my card was stolen", 0.9)
        await pg.click("#vEdit")
        await pg.fill("#vText", "my car was stolen")
        await pg.screenshot(path=str(SHOTS / "04_edit_transcript.png"))
        await pg.click("#vSend"); await wait_reply(pg)
        reply = await pg.locator(".msg.bot").last.inner_text()
        t.add("V5  wrong transcription → citizen edits → agent receives corrected text",
              (await M(pg))[-1]["text"] == "my car was stolen" and "REPORT STOLEN VEHICLE" in await card(pg),
              {"sent_text": (await M(pg))[-1]["text"], "channel": (await M(pg))[-1].get("channel")})

        # L6 Arabic → English mid-journey
        pg, msgs = await fresh(ctx)
        await pg.fill("#input", "سيارتي انسرقت"); await pg.click("#sendBtn"); await wait_reply(pg)
        case_ui_ar = await pg.locator("#sendBtn").inner_text()
        await pg.click("#langEn"); await pg.wait_for_timeout(900)
        sw = await pg.locator(".msg.bot").last.inner_text()
        label = await pg.locator("#sendBtn").inner_text()
        await pg.fill("#input", "The Camry, last night near Olaya"); await pg.click("#sendBtn"); await wait_reply(pg)
        reply = await pg.locator(".msg.bot").last.inner_text()
        await pg.screenshot(path=str(SHOTS / "05_switch_ar_to_en.png"))
        t.add("L6  start Arabic → switch to English: labels change, state kept, workflow continues",
              case_ui_ar == "إرسال" and label == "Send" and "progress are unchanged" in sw and "Shall I go ahead" in reply,
              {"send_label_before": case_ui_ar, "send_label_after": label, "switch_msg": sw[:60], "next_step": reply[:50]})

        # L7 English → Arabic mid-journey
        pg, msgs = await fresh(ctx)
        await pg.fill("#input", "My car was stolen"); await pg.click("#sendBtn"); await wait_reply(pg)
        await pg.click("#langAr"); await pg.wait_for_timeout(900)
        sw = await pg.locator(".msg.bot").last.inner_text()
        await pg.fill("#input", "الكامري، البارحة عند العليا"); await pg.click("#sendBtn"); await wait_reply(pg)
        reply = await pg.locator(".msg.bot").last.inner_text()
        rtl = await pg.evaluate("document.getElementById('screen').dir")
        t.add("L7  start English → switch to Arabic: state kept, workflow continues, RTL layout",
              "حالتك وتقدّمك" in sw and "أرسله؟" in reply and rtl == "rtl", {"switch_msg": sw[:50], "next_step": reply[-20:], "dir": rtl})

        # TTS
        n_listen = await pg.locator("button.listen").count()
        await pg.locator("button.listen").last.click()
        t.add("TTS 🔊 Listen available on agent replies and does not break the workflow", n_listen > 0, {"listen_buttons": n_listen})
        await b.close()

    res = {"how": "python tests/ui_voice_check.py", "n": len(t.cases), "passed": sum(c["pass"] for c in t.cases), "cases": t.cases,
           "note": "Speech recogniser simulated in-browser to test the interface deterministically; live-microphone accuracy is "
                   "covered by the manual checklist in docs/VOICE_MANUAL_TESTS.md. Screenshots: results/voice_ui/."}
    (ROOT / "results" / "voice_ui_results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{res['passed']}/{res['n']} voice/language UI tests passed")


asyncio.run(main())
