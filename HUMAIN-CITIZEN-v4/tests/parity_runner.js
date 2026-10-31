// Runs conversation scripts through the in-browser engine (Node) and prints per-turn results as JSON.
const H = require("../web/engine/humain.js");
const scripts = JSON.parse(require("fs").readFileSync(0, "utf8"));
const out = scripts.map(sc => {
  const a = new H.Agent(sc.persona || "omar");
  return sc.steps.map(s => {
    let r;
    if (typeof s === "string") r = a.handle(s === "OTP" ? a.otp : s);
    else if (s[0] === "tick") r = a.tick(s[1]);
    else if (s[0] === "config") { a.configure(s[1]); return null; }
    else if (s[0] === "lang") r = a.setLanguage(s[1]);
    else if (s[0] === "voice") r = a.handle(s[1], "voice", s[2]);
    return { reply: r.reply, mode: r.mode, types: r.trace.map(e => e.type), quick: r.quick_replies, lang: r.lang,
             tools: Object.values(a.backends.records).map(x => x.tool).sort(), notes: r.notifications.map(n => n.text) };
  }).filter(Boolean);
});
process.stdout.write(JSON.stringify(out));
