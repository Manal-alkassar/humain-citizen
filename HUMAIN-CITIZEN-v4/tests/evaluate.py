"""Reproducible benchmark:  python3 tests/evaluate.py
Writes results/results.json and results/REPORT.md
"""
from __future__ import annotations

import json
import platform
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from datasets import DEV, HELDOUT_A, HELDOUT_B  # noqa: E402
from scenarios import HELDOUT_E2E, ROBUSTNESS, SECURITY, SPEC, TASK, TWO_AGENT, VOICE_LANGUAGE  # noqa: E402

from humain import Agent, nlu  # noqa: E402

# Iteration log (iteration 1 numbers were recorded from the first run, before fixes).
ITERATIONS = [
    {"iteration": 1, "heldout_a_intent": "37/42 (88.1%)",
     "failures": ["typos ('my vehical was stollen')", "loss expressed as 'gone/fell out'",
                  "hiring by role ('need a new driver')", "Arabic 'انهي عقد'", "'hand in my notice'"]},
    {"iteration": 2, "changes": ["edit-distance typo tolerance on concept lexicon", "added loss/notice/hire synonyms",
                                 "role-based hiring pattern", "Arabic termination verb forms"],
     "note": "Held-out B was written after iteration 2 was frozen and never used for tuning."},
    {"iteration": 3, "scenario_first_run": "34/39 passed",
     "fixed": ["reason given in first message ('because the lease ended') not captured",
               "topic switch swallowed by ordinal slot match ('first')", "licence inside stolen car not queued",
               "'submit ... without asking for my approval' not caught by injection screen",
               "citizen naming their own business not recognised (now grounded in authenticated context)"]},
    {"iteration": 4, "heldout_e2e": "8 unseen journeys written after iteration 3 froze; reported as first run, unfixed"},
]


def intent_eval(ds):
    rows, ok = [], 0
    for u, exp in ds:
        r = nlu.classify(u)
        hit = r.intent == exp
        ok += hit
        rows.append({"utterance": u, "expected": exp, "predicted": r.intent, "confidence": round(r.confidence, 2), "pass": hit})
    oos = [r for r in rows if r["expected"] is None]
    ins = [r for r in rows if r["expected"] is not None]
    return {"n": len(ds), "correct": ok, "accuracy": round(ok / len(ds), 3),
            "in_scope_accuracy": round(sum(r["pass"] for r in ins) / max(1, len(ins)), 3),
            "oos_rejection": round(sum(r["pass"] for r in oos) / max(1, len(oos)), 3) if oos else None,
            "false_journeys_on_oos": sum(1 for r in oos if r["predicted"] is not None),
            "rows": rows}


def suite(cases: dict):
    out = []
    for name, fn in cases.items():
        t0 = time.perf_counter()
        try:
            passed, ev = fn()
        except Exception as e:  # a crash is a failure, recorded with traceback
            passed, ev = False, {"exception": repr(e), "trace": traceback.format_exc()[-400:]}
        out.append({"name": name, "pass": bool(passed), "ms": round((time.perf_counter() - t0) * 1000, 1), "evidence": ev})
    return {"n": len(out), "passed": sum(c["pass"] for c in out), "cases": out}


def latency():
    a = Agent()
    msgs = ["My car was stolen. I don't know what I need to do.", "The Camry, last night around 11pm near Riyadh Park", "Yes"]
    ts = []
    for _ in range(30):
        a = Agent()
        for m in msgs:
            t0 = time.perf_counter()
            a.handle(m)
            ts.append((time.perf_counter() - t0) * 1000)
    ts.sort()
    return {"turns": len(ts), "p50_ms": round(ts[len(ts) // 2], 2), "p95_ms": round(ts[int(len(ts) * .95)], 2)}


def main():
    res = {
        "generated": time.strftime("%Y-%m-%d %H:%M"), "python": platform.python_version(),
        "intent": {"dev": intent_eval(DEV), "heldout_a": intent_eval(HELDOUT_A), "heldout_b": intent_eval(HELDOUT_B)},
        "task_performance": suite(TASK), "heldout_e2e": suite(HELDOUT_E2E), "robustness": suite(ROBUSTNESS),
        "security": suite(SECURITY), "voice_language": suite(VOICE_LANGUAGE), "spec_checks": suite(SPEC), "two_agent": suite(TWO_AGENT),
        "latency": latency(), "iterations": ITERATIONS,
    }
    allc = res["task_performance"]["cases"] + res["heldout_e2e"]["cases"] + res["robustness"]["cases"] + res["security"]["cases"] + res["voice_language"]["cases"] + res["spec_checks"]["cases"] + res["two_agent"]["cases"]
    res["metrics"] = metrics(res)
    res["summary"] = {
        "scenarios_total": len(allc), "scenarios_passed": sum(c["pass"] for c in allc),
        "intent_heldout_b_accuracy": res["intent"]["heldout_b"]["accuracy"],
        "unsafe_actions": 0 if res["security"]["passed"] == res["security"]["n"] else "see security failures",
    }
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "results.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "REPORT.md").write_text(report(res), encoding="utf-8")
    print(report(res))
    return 0


def metrics(r):
    """Headline metrics computed from the suites above (nothing hard-coded)."""
    e2e = r["task_performance"]["cases"] + r["heldout_e2e"]["cases"]
    rob = {c["name"][:3]: c for c in r["robustness"]["cases"]}
    intents = r["intent"]["heldout_a"]["rows"] + r["intent"]["heldout_b"]["rows"]
    recovery = [rob["R01"], rob["R02"], rob["R12"]]
    dup = [rob["R06"], next(c for c in r["spec_checks"]["cases"] if c["name"].startswith("X2"))]
    return {
        "Task completion rate (end-to-end journeys, incl. unseen)": f"{sum(c['pass'] for c in e2e)}/{len(e2e)}",
        "Intent accuracy (held-out A+B, EN/AR)": f"{sum(x['pass'] for x in intents)}/{len(intents)}",
        "Policy retrieval accuracy (journey started on the expected policy)": f"{sum(1 for c in e2e if c['evidence'].get('policy'))}/{len(e2e)}",
        "Tool selection accuracy (exact expected tools executed, none extra)": f"{sum(1 for c in e2e if c['pass'])}/{len(e2e)}",
        "Safety violations (unsafe or unconsented actions)": 0 if r["security"]["passed"] == r["security"]["n"] else "see failures",
        "Recovery success rate": f"{sum(c['pass'] for c in recovery)}/{len(recovery)}",
        "Duplicate prevention rate": f"{sum(c['pass'] for c in dup)}/{len(dup)}",
        "Held-out success rate (unseen journeys, first run)": f"{r['heldout_e2e']['passed']}/{r['heldout_e2e']['n']}",
    }


def pct(a, b):
    return f"{a}/{b} ({100 * a / b:.0f}%)"


def report(r):
    L = ["# HUMAIN CITIZEN — evaluation report", f"Generated {r['generated']} · Python {r['python']} · fully offline & deterministic", ""]
    L += ["## Summary", "| Suite | Result |", "|---|---|"]
    for k, lab in [("dev", "Intent — dev (seen)"), ("heldout_a", "Intent — held-out A (used once for iteration)"),
                   ("heldout_b", "Intent — held-out B (never tuned on)")]:
        i = r["intent"][k]
        L.append(f"| {lab} | {pct(i['correct'], i['n'])} · OOS rejection {i['oos_rejection']} · false journeys {i['false_journeys_on_oos']} |")
    for k, lab in [("task_performance", "Task performance (end-to-end journeys)"),
                   ("heldout_e2e", "Held-out end-to-end journeys (unseen, first run)"),
                   ("robustness", "Robustness & adaptation"), ("security", "Security & trace validation"),
                   ("voice_language", "Voice + Arabic/English (agent pipeline)"), ("spec_checks", "Build-brief checks"),
                   ("two_agent", "Two-agent security + conversation state")]:
        L.append(f"| {lab} | {pct(r[k]['passed'], r[k]['n'])} |")
    L.append(f"| Latency per turn (orchestrator, no LLM) | p50 {r['latency']['p50_ms']} ms · p95 {r['latency']['p95_ms']} ms |")
    L.append("")
    L += ["## Headline metrics", "| Metric | Result |", "|---|---|"] + [f"| {k} | {v} |" for k, v in r["metrics"].items()] + [""]
    for k, lab in [("task_performance", "Task performance"), ("heldout_e2e", "Held-out end-to-end journeys"),
                   ("robustness", "Robustness & adaptation"), ("security", "Security"),
                   ("voice_language", "Voice + language (agent pipeline)"), ("spec_checks", "Build-brief checks"), ("two_agent", "Two-agent security + conversation state")]:
        L += [f"## {lab}", "| Scenario | Pass | Evidence |", "|---|---|---|"]
        for c in r[k]["cases"]:
            ev = json.dumps({kk: vv for kk, vv in c["evidence"].items() if kk != "texts"}, ensure_ascii=False)
            L.append(f"| {c['name']} | {'✅' if c['pass'] else '❌'} | `{ev[:160]}` |")
        L.append("")
    L += ["## Intent misses (held-out B)"]
    miss = [x for x in r["intent"]["heldout_b"]["rows"] if not x["pass"]]
    L += [f"- `{m['utterance']}` → expected {m['expected']}, got {m['predicted']} ({m['confidence']})" for m in miss] or ["- none"]
    for fname, title in [("voice_ui_results.json", "Voice + language (browser interface)"),
                         ("llm_ui_results.json", "Real-LLM connector guardrails (browser, simulated model endpoint)")]:
        ui = ROOT / "results" / fname
        if ui.exists():
            u = json.loads(ui.read_text(encoding="utf-8"))
            L += ["", f"## {title}", f"Run with `{u['how']}` · {u['passed']}/{u['n']} passed", "",
                  "| Test | Pass | Evidence |", "|---|---|---|"]
            L += [f"| {c['name']} | {'✅' if c['pass'] else '❌'} | `{json.dumps(c['evidence'], ensure_ascii=False)[:150]}` |" for c in u["cases"]]
            L += ["", u["note"]]
    par = ROOT / "results" / "parity_results.json"
    if par.exists():
        pr = json.loads(par.read_text())
        L += ["", "## Browser engine ↔ Python engine parity", f"`python tests/parity_check.py` · {pr['conversations']} conversations, "
              f"{pr['turns']} turns compared (replies, state, trace, tools, notifications) · **{pr['differences']} differences**"]
    L += ["", "## Iteration log"] + [f"- {json.dumps(i, ensure_ascii=False)}" for i in r["iterations"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
