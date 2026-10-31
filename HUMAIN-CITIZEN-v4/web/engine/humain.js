/* HUMAIN CITIZEN agent engine, in-browser edition.
 * Line-for-line port of the Python package (humain/). Same policies, rules, guardrails and templates
 * (loaded from data.js, generated from the Python source). Parity is checked by tests/parity_check.py.
 * Runs with no server: open web/index.html directly, or host the web/ folder on any static host.
 */
(function (root) {
  "use strict";
  const D = (typeof HUMAIN_DATA !== "undefined") ? HUMAIN_DATA : require("./data.js");

  // ------------------------------------------------------------------ SHA-256 / HMAC (sync, no deps)
  function utf8(s) { return new TextEncoder().encode(s); }
  function sha256Bytes(bytes) {
    const K = new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
    const H = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
    const l = bytes.length, nb = ((l + 9 + 63) >> 6) << 6, m = new Uint8Array(nb);
    m.set(bytes); m[l] = 0x80;
    const bits = l * 8; const dv = new DataView(m.buffer);
    dv.setUint32(nb - 4, bits >>> 0); dv.setUint32(nb - 8, Math.floor(bits / 4294967296));
    const W = new Uint32Array(64);
    for (let o = 0; o < nb; o += 64) {
      for (let i = 0; i < 16; i++) W[i] = dv.getUint32(o + i * 4);
      for (let i = 16; i < 64; i++) {
        const a = W[i - 15], b = W[i - 2];
        const s0 = ((a >>> 7) | (a << 25)) ^ ((a >>> 18) | (a << 14)) ^ (a >>> 3);
        const s1 = ((b >>> 17) | (b << 15)) ^ ((b >>> 19) | (b << 13)) ^ (b >>> 10);
        W[i] = (W[i - 16] + s0 + W[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = H;
      for (let i = 0; i < 64; i++) {
        const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + S1 + ch + K[i] + W[i]) >>> 0;
        const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        const mj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + mj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
    }
    const out = new Uint8Array(32); const ov = new DataView(out.buffer);
    for (let i = 0; i < 8; i++) ov.setUint32(i * 4, H[i]);
    return out;
  }
  const hex = b => Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
  const sha256 = s => hex(sha256Bytes(utf8(s)));
  function hmac(keyBytes, msg) {
    let k = keyBytes.length > 64 ? sha256Bytes(keyBytes) : keyBytes;
    const pad = new Uint8Array(64); pad.set(k);
    const ip = pad.map(x => x ^ 0x36), op = pad.map(x => x ^ 0x5c), mb = utf8(msg);
    const inner = new Uint8Array(64 + mb.length); inner.set(ip); inner.set(mb, 64);
    const ih = sha256Bytes(inner); const outer = new Uint8Array(96); outer.set(op); outer.set(ih, 64);
    return hex(sha256Bytes(outer));
  }
  function randomBytes(n) {
    const b = new Uint8Array(n);
    if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(b);
    else for (let i = 0; i < n; i++) b[i] = Math.floor(Math.random() * 256);
    return b;
  }

  // ------------------------------------------------------------------ messages
  function t(key, lang, kw) {
    const e = D.templates[key]; let s = e ? (e[lang] ?? e.en) : key;
    if (kw) s = s.replace(/\{(\w+)\}/g, (m, k) => (k in kw ? String(kw[k]) : m));
    return s;
  }

  // ------------------------------------------------------------------ NLU
  const AR_RE = /[؀-ۿ]/;
  function detectLanguage(text) {
    const s = (text || "").replace(/\b[A-Z]{3}\s?\d{4}\b/g, " ");
    const ar = (s.match(/[؀-ۿ]+/g) || []).length;
    const en = (s.match(/[A-Za-z]{2,}/g) || []).length;
    const other = (s.match(/[Ѐ-ӿऀ-ॿ぀-ヿ一-鿿가-힯฀-๿֐-׿]/g) || []).length;
    if (!ar && !en) return { lang: null, mixed: false, supported: other === 0, ar_words: 0, en_words: 0 };
    if (other > Math.max(ar, en)) return { lang: null, mixed: false, supported: false, ar_words: ar, en_words: en };
    let dominant;
    if (ar === en) { const f = s.match(/[؀-ۿ]|[A-Za-z]/); dominant = f && AR_RE.test(f[0]) ? "ar" : "en"; }
    else dominant = ar > en ? "ar" : "en";
    return { lang: dominant, mixed: ar > 0 && en > 0, supported: true, ar_words: ar, en_words: en };
  }
  function normalise(text) {
    let s = (text || "").normalize("NFKC").toLowerCase();
    s = s.replace(/[ً-ْـ]/g, "").replace(/[إأآا]/g, "ا").replace(/ى/g, "ي").replace(/ة/g, "ه").replace(/’/g, "'");
    s = s.replace(/[^\p{L}\p{N}_\s']/gu, " ");
    return s.replace(/\s+/g, " ").trim();
  }
  function ngrams(text) {
    const g = new Map();
    for (const w0 of normalise(text).split(" ").filter(Boolean)) {
      const w = Array.from(" " + w0 + " ");
      for (let n = 3; n <= 5; n++) for (let i = 0; i < Math.max(0, w.length - n + 1); i++) {
        const k = w.slice(i, i + n).join(""); g.set(k, (g.get(k) || 0) + 1);
      }
    }
    return g;
  }
  const INDEX = (() => {
    const docs = []; for (const [intent, ss] of Object.entries(D.exemplars)) for (const s of ss) docs.push([intent, ngrams(s)]);
    const df = new Map(); for (const [, g] of docs) for (const k of g.keys()) df.set(k, (df.get(k) || 0) + 1);
    const n = docs.length, idf = new Map(); for (const [k, v] of df) idf.set(k, Math.log((1 + n) / (1 + v)) + 1);
    const vec = g => { const v = new Map(); let ss = 0; for (const [k, c] of g) { const x = c * (idf.get(k) ?? 1.0); v.set(k, x); ss += x * x; }
      const norm = Math.sqrt(ss) || 1.0; for (const [k, x] of v) v.set(k, x / norm); return v; };
    const vd = docs.map(([i, g]) => [i, vec(g)]);
    return { scores(text) { const q = vec(ngrams(text)); const best = {};
      for (const [intent, d] of vd) { let s = 0; for (const [k, w] of q) s += w * (d.get(k) || 0); best[intent] = Math.max(best[intent] || 0, s); }
      return best; } };
  })();
  function edit1(a, b, k) {
    a = Array.from(a); b = Array.from(b);
    if (Math.abs(a.length - b.length) > k) return false;
    let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      const cur = [i];
      for (let j = 1; j <= b.length; j++) cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0)));
      if (Math.min(...cur) > k) return false; prev = cur;
    }
    return prev[prev.length - 1] <= k;
  }
  const isAlpha = s => /^\p{L}+$/u.test(s);
  const FUZZY = {}; for (const [c, ws] of Object.entries(D.concepts)) FUZZY[c] = ws.filter(w => isAlpha(w) && Array.from(w).length >= 5);
  const ROLE_RE = /\b(need|want|looking for|find)\s+(a|an)\s+(new\s+)?(driver|cashier|accountant|receptionist|engineer|manager|assistant|cleaner|chef|barista|salesperson|technician|nurse|worker|employee)\b(?!.*\b(stolen|lost|stole)\b)/;
  function conceptHits(text) {
    const tt = " " + normalise(text) + " ", raw = " " + (text || "").toLowerCase() + " ", hits = new Set();
    for (const [c, words] of Object.entries(D.concepts)) for (const w of words) {
      const wn = AR_RE.test(w) ? normalise(w) : w;
      if (tt.includes(wn) || raw.includes(w)) { hits.add(c); break; }
    }
    for (const tok of tt.split(" ").filter(Boolean)) {
      if (Array.from(tok).length < 5 || !isAlpha(tok)) continue;
      for (const [c, words] of Object.entries(FUZZY))
        if (!hits.has(c) && words.some(w => edit1(tok, w, Array.from(w).length < 8 ? 1 : 2))) hits.add(c);
    }
    if (ROLE_RE.test(raw)) hits.add("HIRE");
    return hits;
  }
  const round = (x, d) => { const f = Math.pow(10, d); return Math.round(x * f) / f; };
  function classify(text, extra) {
    const tf = INDEX.scores(text); const hits = conceptHits(text); for (const e of (extra || [])) hits.add(e);
    const combined = {};
    for (const intent of D.intents) {
      const [a, b] = D.pairs[intent]; let concept = 0.0;
      if (hits.has(a) && (b === null || hits.has(b))) concept = 1.0;
      else if (hits.has(a) || (b && hits.has(b))) concept = 0.35;
      combined[intent] = round(0.45 * (tf[intent] || 0) + 0.55 * concept, 4);
    }
    if (hits.has("LICENCE") && !hits.has("VEHICLE")) combined.report_stolen_vehicle *= 0.5;
    const ranked = Object.entries(combined).sort((x, y) => y[1] - x[1]);
    const [top, conf] = ranked[0];
    const secondary = ranked.slice(1).filter(([i, s]) => s >= 0.6 && i !== "case_status").map(([i]) => i);
    const concepts = Array.from(hits).sort();
    const engine = "deterministic-tfidf+lexicon";
    if (conf < D.threshold) return { intent: null, confidence: conf, scores: combined, secondary: [], concepts, engine };
    return { intent: top, confidence: conf, scores: combined, secondary, concepts, engine };
  }
  const TIME_RES = D.time_patterns.map(([p, l]) => [new RegExp(p), l]);
  function extractTime(text) {
    const s = normalise(text), found = [];
    for (const [re, label] of TIME_RES) { const m = s.match(re); if (m) { const v = (label || m[0]).trim(); if (!found.some(f => f.includes(v))) found.push(v); } }
    return found.length ? Array.from(new Set(found)).join(", ") : null;
  }
  const LOC_RE = /\b(?:near|outside|in front of|at|in|from|by|opposite)\s+(?:the\s+)?([a-z][\p{L}\p{N}_' ]{2,40}?)(?=\s+(?:at|around|about|last|yesterday|this|today|on|tonight|and|but|when|i|it|\d)\b|[.,!?]|$)/giu;
  const LOC_STOP = new Set(["night", "morning", "evening", "the morning", "pm", "am", "time", "all", "work"]);
  const AR_LOC_RE = /(?:في|عند|قرب|جنب|قدام|من)\s+([؀-ۿ ]{2,30})/;
  function extractLocation(text) {
    for (const m of (text || "").matchAll(LOC_RE)) {
      const cand = m[1].trim();
      if (LOC_STOP.has(cand.toLowerCase()) || /^\d/.test(cand)) continue;
      if (["my", "a", "an", "about", "around"].includes(cand.toLowerCase().split(/\s+/)[0])) continue;
      return cand;
    }
    const m = (text || "").match(AR_LOC_RE);
    if (m) { const cand = m[1].trim().split(/\s+(?:امس|البارحه|البارحة|اليوم|الساعه|الساعة)/)[0]; if (cand) return cand; }
    return null;
  }
  function matchOption(text, options, keys) {
    const s = normalise(text);
    const ordinals = [["first", "1st", "one", "الاول", "الأول", "1"], ["second", "2nd", "two", "الثاني", "الثانيه", "2"]];
    const hits = [];
    options.forEach((o, i) => { for (const k of keys) { const v = String(o[k] ?? "").toLowerCase();
      if (v && (s.includes(v) || s.includes(normalise(v)) || s.replace(/ /g, "").includes(v.replace(/ /g, "")))) { hits.push(i); break; } } });
    if (new Set(hits).size === 1) return hits[0];
    const words = new Set(s.split(" "));
    for (let i = 0; i < ordinals.length; i++) if (i < options.length && ordinals[i].some(w => words.has(w))) return i;
    return null;
  }
  const YES = new Set(["yes", "y", "yeah", "yep", "confirm", "confirmed", "submit", "go ahead", "proceed", "ok", "okay", "sure", "do it", "please do", "approve", "approved", "نعم", "ايوه", "ايه", "اي", "موافق", "تمام", "اكد", "ارسل", "يب"]);
  const NO = new Set(["no", "n", "nope", "cancel", "stop", "don't", "dont", "abort", "not now", "لا", "الغاء", "الغي", "وقف", "لاحقا"]);
  function yesNo(text) {
    const s = normalise(text), words = s.split(" ").filter(Boolean), joined = " " + s + " ";
    if ([...NO].some(n => n.includes(" ") && joined.includes(" " + n + " ")) || words.some(w => NO.has(w))) return false;
    if ([...YES].some(y => y.includes(" ") && joined.includes(" " + y + " ")) || words.some(w => YES.has(w))) return true;
    return null;
  }
  function extractEnum(text, type) {
    const s = normalise(text);
    if (type === "enum:lost|stolen|damaged") {
      if (/damag|broke|torn|تالف|انكسر|مقطوع/.test(s)) return "damaged";
      if (/stol|theft|سرق|مسروق/.test(s)) return "stolen";
      if (/lost|lose|misplac|can'?t find|cannot find|ضاع|فقد|ضيع/.test(s)) return "lost";
    }
    if (type === "enum:pickup|delivery") {
      if (/deliver|home|send|post|courier|توصيل|البيت|المنزل|ارسل/.test(s)) return "delivery";
      if (/pick|collect|branch|office|myself|استلم|الفرع|بنفسي/.test(s)) return "pickup";
    }
    return null;
  }
  function extractMoney(text) {
    const m = (text || "").toLowerCase().match(/(\d[\d,.]*)\s*(k\b|sar|riyal|ريال)?/);
    if (!m) return null;
    const amount = parseFloat(m[1].replace(/,/g, "")) * (m[2] === "k" ? 1000 : 1);
    if (isNaN(amount)) return null;
    return `${Math.trunc(amount)} SAR`;
  }
  const PLACE_RE = /\b(mall|street|road|park|station|airport|parking|car park|office|home|mosque|souq|market|hospital|gym|school|university)\b|مول|شارع|طريق|حي |موقف|مواقف|البيت|المطار|الدوام|المسجد|السوق|محطه|بارك|المستشفي|الجامعه|المدرسه|النادي/;
  const nlu = { PLACE_RE, detectLanguage, normalise, classify, extractTime, extractLocation, matchOption, yesNo, extractEnum, extractMoney, conceptHits };

  // ------------------------------------------------------------------ guardrails
  const INJ = D.injection_patterns.map(p => new RegExp(p, "i"));
  const READ_ONLY = new Set(["case.get_status", "identity.verify_identity", "vehicle.get_user_vehicles", "vehicle.verify_vehicle", "commerce.verify_registration",
    "traffic.check_licence_status", "traffic.find_branch_slot", "hr.verify_employee", "hr.check_existing_contract"]);
  function screenInput(text) { const s = text || "", sn = normalise(s); return D.injection_patterns.filter((p, i) => INJ[i].test(s) || INJ[i].test(sn)); }
  const PII = [[/\b([12])\d{5}(\d{4})\b/g, "$1******$2"], [/\+?966\s?5\d(\d{3})(\d{4})/g, "+9665X***$2"],
    [/\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?(\d{4})\b/g, "****-****-****-$1"], [/[\w.+-]+@[\w-]+\.[\w.]+/g, "<email>"]];
  function redact(v) {
    if (typeof v === "string") { for (const [re, rep] of PII) v = v.replace(re, rep); return v; }
    if (Array.isArray(v)) return v.map(redact);
    if (v && typeof v === "object") { const o = {}; for (const [k, x] of Object.entries(v)) o[k] = redact(x); return o; }
    return v;
  }
  class GuardrailViolation extends Error { constructor(code, detail) { super(code + ": " + detail); this.code = code; this.detail = detail; } }
  const ALLOW = new Set(Object.keys(D.tool_schemas));
  function checkToolCall(tool, args, journeyTools) {
    if (!ALLOW.has(tool)) throw new GuardrailViolation("TOOL_NOT_ALLOWLISTED", tool);
    if (!journeyTools.has(tool) && !READ_ONLY.has(tool)) throw new GuardrailViolation("TOOL_OUT_OF_SCOPE", tool + " is not part of the active journey");
    const schema = D.tool_schemas[tool].required;
    const extra = Object.keys(args).filter(k => !(k in schema));
    if (extra.length) throw new GuardrailViolation("DATA_MINIMISATION", "unexpected fields " + JSON.stringify(extra.sort()));
    for (const [f, rx] of Object.entries(schema)) {
      const v = args[f];
      if (v === null || v === undefined || !new RegExp(rx).test(String(v))) throw new GuardrailViolation("SCHEMA_INVALID", `${tool}.${f}=${JSON.stringify(redact(String(v)))}`);
    }
  }
  const SECRET = randomBytes(32);   // held by the engine, never part of any model prompt
  const planHash = (caseId, plan) => sha256(JSON.stringify({ case: caseId, plan: plan.map(s => [s.tool, s.args]) })).slice(0, 16);
  const issueConsent = (caseId, ph) => hmac(SECRET, `${caseId}:${ph}`).slice(0, 24);
  const verifyConsent = (tok, caseId, ph) => !!tok && tok === issueConsent(caseId, ph);
  // Deterministic authorization: the authenticated session is the only source of identity.
  function authorize(tool, args, sessionNid, record) {
    if (!sessionNid || !record) return [false, "UNAUTHENTICATED", "no authenticated session"];
    for (const [k, v] of Object.entries(args)) if ((k === "nid" || k.endsWith("_nid")) && v !== sessionNid) return [false, "SCOPE_DENIED", `${k} does not match the authenticated citizen`];
    if (READ_ONLY.has(tool)) return [true, "OK", "read-only, citizen scope matches"];
    if ("plate" in args && !(record.vehicles || []).some(v => v.plate === args.plate)) return [false, "BOLA_DENIED", "vehicle.owner_id != session.citizen_id"];
    for (const k of ["cr", "employer_cr"]) if (k in args && !(record.commercial_registrations || []).some(c => c.cr === args[k])) return [false, "BOLA_DENIED", "registration.owner_id != session.citizen_id"];
    if ("licence_no" in args && args.licence_no !== (record.licence || {}).number) return [false, "BOLA_DENIED", "licence.holder_id != session.citizen_id"];
    return [true, "OK", "owner_id == session.citizen_id for every object"];
  }
  const guardrails = { screenInput, redact, checkToolCall, GuardrailViolation, planHash, issueConsent, verifyConsent, authorize };

  // ------------------------------------------------------------------ policy
  class PolicyStore {
    constructor() { this.byIntent = {}; for (const p of D.policy_kb.policies) this.byIntent[p.intent] = p; this.pinned = {}; this.today = "2026-10-31"; }
    setDate(d) { this.today = d; }
    pin(intent, v) { if (v == null) delete this.pinned[intent]; else this.pinned[intent] = v; }
    retrieve(intent) {
      const p = this.byIntent[intent]; if (!p) return null;
      const vs = p.versions.slice().sort((a, b) => a.effective < b.effective ? -1 : a.effective > b.effective ? 1 : 0);
      let v; if (intent in this.pinned) v = vs.find(x => x.version === this.pinned[intent]);
      else { const inf = vs.filter(x => x.effective <= this.today); v = inf.length ? inf[inf.length - 1] : vs[0]; }
      return Object.assign({ policy_id: p.id, file: p.file, title: p.title, authority: p.authority, action: p.action }, JSON.parse(JSON.stringify(v)), { retrieved_on: this.today });
    }
  }
  const crOf = (ctx, s) => ctx.commercial_registrations.find(c => c.cr === s.cr) || {};
  const RULES = {
    identity_verified: (ctx) => ctx.identity_verified,
    owns_selected_vehicle: (ctx, s) => ctx.vehicles.some(v => v.plate === s.vehicle),
    vehicle_not_already_reported: (ctx, s, be) => !be.reportedPlates.has(s.vehicle),
    licence_valid_or_recent: (ctx) => ctx.licence.status === "valid" || (ctx.licence.status === "expired" && ctx.licence.expires >= "2025-10-31"),
    owns_selected_cr: (ctx, s) => ctx.commercial_registrations.some(c => c.cr === s.cr),
    cr_no_active_employees: (ctx, s) => (crOf(ctx, s).active_employees ?? 1) === 0,
    cr_no_outstanding_obligations: (ctx, s) => (crOf(ctx, s).outstanding_obligations ?? ["?"]).length === 0,
    owns_any_cr: (ctx) => ctx.commercial_registrations.length > 0,
    has_employer: (ctx) => !!ctx.employer,
  };
  const CONDITIONS = {
    vehicle_insured: (ctx, s) => ctx.vehicles.some(v => v.plate === s.vehicle && v.insured),
    photo_older_than_5y: (ctx) => parseInt(ctx.licence.photo_year, 10) <= 2021,
  };

  // ------------------------------------------------------------------ gateway + mock back-ends
  class ToolTimeout extends Error {} class ToolUnavailable extends Error {} class CircuitOpen extends Error {}
  class ToolRejected extends Error { constructor(r) { super(r); this.reason = r; } }
  const statusRank = (tool, st) => D.progressions[tool][1].map(x => x[1]).indexOf(st);
  class MockBackends {
    constructor(clock, registry) { this.clock = clock; this.registry = registry || {}; this.failures = {}; this.records = {}; this.idempotency = {}; this.reportedPlates = new Set(); this.callLog = []; this.seq = 100001; }
    inject(tool, modes) { this.failures[tool] = modes === "down" ? "down" : modes.slice(); }
    clearFailures() { this.failures = {}; }
    maybeFail(tool) {
      const f = this.failures[tool];
      if (f === "down") throw new ToolUnavailable(`${tool} returned 503`);
      if (f && f.length) { const mode = f.shift();
        if (mode === "timeout") throw new ToolTimeout(`${tool} timed out after 8s`);
        if (mode === "error") throw new ToolUnavailable(`${tool} returned 502`);
        if (mode.startsWith("reject:")) throw new ToolRejected(mode.split(":").slice(1).join(":")); }
    }
    call(tool, args, idem) {
      this.callLog.push({ tool, idem, t: this.clock.now });
      const rec0 = this.registry[args.nid] || {};
      if (tool === "identity.verify_identity") return { verified: !!(this.registry[args.nid] && rec0.identity_verified), source: "mock identity service" };
      if (tool === "vehicle.get_user_vehicles") return { vehicles: (rec0.vehicles || []).map(v => v.plate) };
      if (tool === "vehicle.verify_vehicle") return { owner_match: (rec0.vehicles || []).some(v => v.plate === args.plate), reported_stolen: this.reportedPlates.has(args.plate) };
      if (tool === "commerce.verify_registration") return { owner_match: (rec0.commercial_registrations || []).some(c => c.cr === args.cr) };
      if (tool === "traffic.check_licence_status") { const lic = rec0.licence || {}; return { licence_status: lic.status, expires: lic.expires, photo_year: lic.photo_year }; }
      if (tool === "traffic.find_branch_slot") return { branch: "Olaya service branch (mock)", slot: "Sun 2 Nov 2026, 10:30" };
      if (tool === "hr.verify_employee") return { eligible_to_contract: true, source: "mock labour registry" };
      if (tool === "hr.check_existing_contract") return { existing_contract: false };
      if (tool === "case.get_status") { const r = this.records[args.ref]; if (!r) throw new ToolRejected("unknown reference");
        return { ref: r.ref, status: r.status, history: r.history }; }
      this.maybeFail(tool);
      if (idem in this.idempotency) return Object.assign({}, this.idempotency[idem], { duplicate: true });
      if (tool === "police.file_theft_report" && this.reportedPlates.has(args.plate)) throw new ToolRejected("vehicle already has an open theft report");
      const [prefix, prog] = D.progressions[tool]; const ref = `${prefix}-${this.seq++}`;
      const rec = { ref, tool, created: this.clock.now, status: prog[0][1], history: [{ t: this.clock.now, status: prog[0][1] }] };
      this.records[ref] = rec;
      if (tool === "police.file_theft_report") this.reportedPlates.add(args.plate);
      const resp = { ref, status: rec.status }; this.idempotency[idem] = resp; return Object.assign({}, resp);
    }
    advance() { for (const r of Object.values(this.records)) { const prog = D.progressions[r.tool][1], el = this.clock.now - r.created;
      const tgt = prog.filter(([t]) => el >= t).slice(-1)[0][1]; if (tgt !== r.status) { r.status = tgt; r.history.push({ t: this.clock.now, status: tgt }); } } }
    submissions(tool) { return Object.values(this.records).filter(r => r.tool === tool).length; }
  }
  class ResilientClient {
    constructor(be, clock) { this.be = be; this.clock = clock; this.breakers = {}; this.authz = null; this.MAX = 3; this.BACKOFF = [0, 2, 4]; this.TH = 3; this.COOL = 30; }
    breaker(tool) { const k = tool.split(".")[0]; return this.breakers[k] || (this.breakers[k] = { failures: 0, open_until: -1 }); }
    call(tool, args, idem, journeyTools, onEvent) {
      checkToolCall(tool, args, journeyTools);
      if (this.authz) { const [ok, code, why] = this.authz(tool, args); if (!ok) throw new GuardrailViolation(code, why); }
      const br = this.breaker(tool);
      if (br.open_until > this.clock.now) throw new CircuitOpen(`circuit open for ${tool.split(".")[0]} until t+${br.open_until.toFixed(0)}m`);
      let last = null;
      for (let attempt = 1; attempt <= this.MAX; attempt++) {
        try { const res = this.be.call(tool, args, idem); br.failures = 0;
          onEvent && onEvent("attempt_ok", { tool, attempt, idempotency_key: idem }); return res; }
        catch (e) {
          if (e instanceof ToolRejected) throw e;
          if (!(e instanceof ToolTimeout || e instanceof ToolUnavailable)) throw e;
          last = e; br.failures += 1;
          onEvent && onEvent("attempt_failed", { tool, attempt, error: e.message, next_backoff_s: attempt < this.MAX ? this.BACKOFF[attempt] : null, idempotency_key: idem });
          if (br.failures >= this.TH) { br.open_until = this.clock.now + this.COOL; throw new CircuitOpen(`${e.message}; circuit opened for ${tool.split(".")[0]}`); }
        }
      }
      throw new ToolUnavailable(String(last && last.message));
    }
  }

  // ------------------------------------------------------------------ Agent 2: Security Agent
  const ROLE_FOR = { report_stolen_vehicle: "citizen", replace_lost_licence: "citizen", leave_job: "citizen", close_business: "establishment_owner", hire_employee: "establishment_owner", end_employee_contract: "establishment_owner" };
  const FREE_TEXT = ["incident_time", "incident_location", "reason", "employee", "job_title", "last_day"];
  function securityReview(plan, policy, intent, session, record, slots, backends, guardEvents) {
    const checks = []; const add = (check, ok, detail, hard = true) => checks.push({ check, result: ok ? "pass" : (hard ? "FAIL" : "WARN"), detail });
    const nid = session.nid;
    add("authenticated_identity", !!record.identity_verified, "session identity from host-app login (mock SSO)");
    add("citizen_scope", plan.every(s => Object.entries(s.args).every(([k, v]) => !(k === "nid" || k.endsWith("_nid")) || v === nid)), "every *_nid argument equals the authenticated citizen");
    const bola = plan.map(s => authorize(s.tool, Object.fromEntries(Object.entries(s.args).filter(([, v]) => !String(v).startsWith("$"))), nid, record));
    add("object_ownership_BOLA", bola.every(b => b[0]), Array.from(new Set(bola.map(b => b[2]))).sort().join("; "));
    add("property_level_BOPLA", plan.every(s => Object.keys(s.args).every(k => k in ((D.tool_schemas[s.tool] || {}).required || {}))), "only the properties each service needs are sent");
    const allowedTools = Array.from(new Set(policy.steps.map(s => s.tool)));
    add("function_authorization_BFLA", plan.every(s => allowedTools.includes(s.tool)), `tools limited to ${JSON.stringify(allowedTools.sort())}`);
    const role = ROLE_FOR[intent] || "citizen"; add("rbac_role", role === "citizen" || record.commercial_registrations.length > 0, `required role: ${role}`);
    add("policy_requirements", policy.eligibility.every(r => !!RULES[r](record, slots, backends)), `${policy.policy_id} v${policy.version} eligibility rules`);
    add("tool_permissions", plan.every(s => ALLOW.has(s.tool)), "all tools on the global allow-list");
    const inj = plan.flatMap(s => Object.entries(s.args).filter(([k]) => FREE_TEXT.includes(k)).map(([, v]) => String(v))).filter(x => screenInput(x).length);
    add("prompt_injection", !inj.length, inj.length ? `suspicious text in ${inj.length} field(s)` : "free-text fields screened");
    add("risk_level", true, `policy risk: ${policy.risk}`, false);
    const needs = "citizen confirmation" + (policy.step_up_auth ? " + step-up authentication" : "");
    add("human_confirmation", true, `required before execution: ${needs}`);
    add("data_access_scope", true, "only the authenticated citizen's own record is used");
    const suspicious = guardEvents >= 3; add("suspicious_behaviour", !suspicious, `${guardEvents} blocked attempt(s) this session`, false);
    const hard = checks.filter(c => c.result === "FAIL").map(c => c.check);
    let decision, reason;
    if (hard.length) { decision = "BLOCK"; reason = hard.join(", "); }
    else if (suspicious) { decision = "ESCALATE"; reason = "repeated blocked attempts in this session"; }
    else if (policy.autonomy === "human_review") { decision = "ESCALATE"; reason = "legally sensitive: only routing to a human officer is permitted"; }
    else { decision = "ALLOW"; reason = `${checks.length} checks passed · ${needs} required`; }
    return { decision, reason, checks, requires: needs, risk: policy.risk };
  }

  // ------------------------------------------------------------------ audit
  class AuditLog {
    constructor() { this.entries = []; }
    body(e) { return JSON.stringify({ data: e.data, kind: e.kind, prev: e.prev, seq: e.seq, t: e.t }); }
    append(time, kind, data) { const prev = this.entries.length ? this.entries[this.entries.length - 1].hash : "GENESIS";
      const e = { seq: this.entries.length, t: round(time, 2), kind, data: redact(data), prev }; e.hash = sha256(this.body(e)).slice(0, 20); this.entries.push(e); return e; }
    verify() { let prev = "GENESIS"; for (const e of this.entries) { if (e.prev !== prev || sha256(this.body(e)).slice(0, 20) !== e.hash) return false; prev = e.hash; } return true; }
    dump() { return JSON.stringify(this.entries); }
  }

  // ------------------------------------------------------------------ agent
  const STATUS_TEXT = D.status_text, STATUS_TEXT_AR = D.status_text_ar, MEANINGFUL = new Set(D.meaningful);
  const statusText = (st, lang) => (lang === "ar" ? STATUS_TEXT_AR : STATUS_TEXT)[st] || st;
  const maskId = s => redact(s);
  const titleCase = s => s.replace(/\b\w/g, c => c.toUpperCase());
  const stripChars = (s, chars) => { let a = 0, b = s.length; while (a < b && chars.includes(s[a])) a++; while (b > a && chars.includes(s[b - 1])) b--; return s.slice(a, b); };
  function mulberry(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let x = Math.imul(seed ^ seed >>> 15, 1 | seed); x = x + Math.imul(x ^ x >>> 7, 61 | x) ^ x; return ((x ^ x >>> 14) >>> 0) / 4294967296; }; }

  const FIELD_LABEL = { vehicle: ["the vehicle details", "بيانات السيارة"], incident_time: ["when you last saw it", "وقت آخر مرة شفتها"],
    incident_location: ["where you last saw it", "مكان آخر مرة شفتها"], loss_reason: ["the reason", "السبب"], delivery_method: ["how you'd like to receive it", "طريقة الاستلام"],
    cr: ["the business", "السجل"], closure_reason: ["the reason", "السبب"], employee: ["the employee", "اسم الموظف"], job_title: ["the job title", "المسمى"],
    monthly_salary: ["the salary", "الراتب"], termination_reason: ["the reason", "السبب"], last_working_day: ["your last day", "آخر يوم دوام"], photo_update: ["the photo confirmation", "تأكيد الصورة"] };
  const NUMW = { a: 1, an: 1, one: 1, two: 2, three: 3, four: 4, five: 5, "couple of": 2 };
  function lastSeenDate(text, today) {
    if (!text) return null; const tt = normalise(text); let back = null;
    if (/today|this morning|tonight|اليوم|الصبح|الفجر|الليله|ساع|hour|minute|دقيق/.test(tt)) back = 0;
    if (/last night|yesterday|امس|البارحه/.test(tt)) back = 1;
    if (/يومين/.test(tt)) back = 2;
    const m = tt.match(/\b(\d+|a|an|one|two|three|four|five|couple of)\s+days?\s+ago/) || tt.match(/قبل\s+(\d+)\s+ايام/);
    if (m) back = /^\d+$/.test(m[1]) ? parseInt(m[1], 10) : (NUMW[m[1]] ?? null);
    if (/اسبوعين|two weeks/.test(tt)) back = 14; else if (/اسبوع|a week ago|one week/.test(tt)) back = 7;
    if (back === null) return null;
    const d = new Date(today + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() - back); return d.toISOString().slice(0, 10);
  }

  class Agent {
    constructor(persona = "omar", seed = 7) {
      this.ctx = JSON.parse(JSON.stringify(D.personas[persona])); this.persona = persona;
      this.policies = new PolicyStore(); this.clock = { now: 0, advance(m) { this.now += m; } };
      this.session = { nid: this.ctx.national_id, source: "host-app SSO (mock)" };   // never taken from chat text or the page
      this.backends = new MockBackends(this.clock, { [this.ctx.national_id]: this.ctx }); this.client = new ResilientClient(this.backends, this.clock);
      this.client.authz = (tool, args) => authorize(tool, args, this.session.nid, this.ctx);
      this.audit = new AuditLog(); this.rng = mulberry(seed);
      this.mode = "idle"; this.active = null; this.journeys = []; this.queue = []; this.notifications = []; this.trace = [];
      this.otp = null; this.otpAttempts = 0; this.ticketSeq = 40001; this.caseSeq = 4821;
      this.lang = null; this.lastDetect = {}; this.voiceMinConfidence = 0.55; this.carry = {};
      this.llmProposal = null;   // set per turn by the host page when a real LLM is connected (validated below)
    }
    emit(kind, title, detail) { const ev = { t: round(this.clock.now, 1), type: kind, title, detail: redact(detail || {}) }; this.trace.push(ev);
      this.audit.append(this.clock.now, kind, Object.assign({ title }, detail || {})); return ev; }
    notify(j, key, text, level = "info") {
      const dk = (j ? j.case_id : "-") + "|" + key;
      if (j) { if (j.notified.has(dk)) return; j.notified.add(dk); }
      const n = { t: round(this.clock.now, 1), case: j ? j.case_id : null, text, level }; this.notifications.push(n); this.emit("NOTIFY", "Push notification sent", n);
    }
    ticket() { return `ESC-${this.ticketSeq++}`; }
    result(reply, quick) { const trace = this.trace; this.trace = [];
      const jj = this.active || this.journeys[this.journeys.length - 1] || null;
      return { reply, trace, mode: this.mode, quick_replies: quick || [], lang: this.lang || "en", detected: this.lastDetect,
        journey: jj ? { intent: jj.intent, case: jj.case_id, policy_id: jj.policy.policy_id, status: jj.status } : null, state: this.workflowState(),
        cases: this.casesSummary(), notifications: this.notifications.slice(-10), clock: this.clock.now }; }

    workflowState() {
      const j = this.active || this.journeys[this.journeys.length - 1] || null; if (!j) return null;
      const st = { intent: j.intent, policy: `${j.policy.file} v${j.policy.version}` };
      if (j.intent === "report_stolen_vehicle") {
        const veh = this.ctx.vehicles.find(v => v.plate === j.slots.vehicle) || null;
        Object.assign(st, { vehicle: veh ? `${veh.make} ${veh.model}` : null, vehicle_make: veh ? veh.make : null, vehicle_model: veh ? veh.model : null, vehicle_plate: veh ? veh.plate : null,
          last_seen_time: j.slots.incident_time || null, last_seen_date: lastSeenDate(j.slots.incident_time, this.policies.today), last_seen_location: j.slots.incident_location || null });
      } else Object.assign(st, j.slots);
      Object.assign(st, { identity_verified: j.flags.identity_verified || false, ownership_verified: j.flags.ownership_verified ?? (j.intent === "leave_job" ? null : false),
        required_information_complete: this.missing(j).length === 0, security_decision: j.flags.security || null,
        citizen_confirmation: j.consent ? "given" : (j.status === "awaiting_consent" ? "requested" : "not yet"), submission_status: j.status, case_id: j.case_id });
      return st;
    }

    handle(text, channel = "text", sttConfidence = null) {
      text = (text || "").trim();
      const det = detectLanguage(text); this.lastDetect = det;
      let lang = this.lang || det.lang || "en";
      if (!text) return this.result(t("empty", lang));
      if (channel === "voice" && sttConfidence !== null && sttConfidence > 0 && sttConfidence < this.voiceMinConfidence) {
        this.emit("GUARD", "Low-confidence transcription — not acted on", { channel: "voice", stt_confidence: sttConfidence, action: "asked citizen to repeat" });
        return this.result(t("unclear_audio", lang));
      }
      if (!det.supported) { this.emit("INPUT", "Unsupported language — asked to use Arabic or English", { channel }); return this.result(t("unsupported_lang", lang)); }
      if (this.lang === null && det.lang) { this.lang = det.lang; lang = this.lang; }
      const inp = { text, channel, detected_language: det.lang, code_switching: det.mixed, conversation_language: lang };
      if (channel === "voice") inp.stt_confidence = sttConfidence;
      this.emit("INPUT", "Citizen message", inp);
      const hits = screenInput(text);
      if (hits.length) { this.emit("GUARD", "Prompt/tool-injection blocked", { signatures: hits.slice(0, 3), action: "refused, no tool call" }); return this.result(t("blocked", lang)); }
      if (this.mode === "confirm") return this.onConfirm(text, lang);
      if (this.mode === "stepup") return this.onStepup(text, lang);
      if (this.mode === "offer_next") {
        const yn = yesNo(text); const nxt = this.queue.length ? this.queue.shift() : null; this.mode = "idle";
        if (yn && nxt) return this.start(nxt, text, lang, null, false);
        if (yn === false) return this.result(nxt ? t("cancelled", lang) : t("fallback", lang));
      }
      if (this.mode === "collecting" && this.active) {
        const res = classify(text, this.contextConcepts(text));
        if (yesNo(text) === false && text.split(/\s+/).length <= 3) return this.cancel(lang);
        const sw = res.intent && ![this.active.intent, "case_status"].includes(res.intent) && res.confidence >= 0.7;
        const filled = sw ? [] : this.fillSlots(this.active, text, true);
        if (!filled.length && res.intent && res.intent !== this.active.intent && res.confidence >= 0.6) {
          this.emit("ADAPT", "Citizen changed topic — parking current journey", { from: this.active.intent, to: res.intent });
          this.active.status = "parked"; this.mode = "idle";
          const r = this.start(res.intent, text, lang); r.reply = t("switch", lang) + "\n\n" + r.reply; return r;
        }
        if (!filled.length) {
          this.active.reask += 1;
          if (this.active.reask >= 3) { const tk = this.ticket(); this.active.escalation = tk;
            this.emit("ESCALATE", "Repeated misunderstanding → human hand-off", { ticket: tk }); this.mode = "idle";
            return this.result(t("handoff_offer", lang, { ticket: tk })); }
          const q = this.question(this.active, this.missing(this.active), lang);
          this.emit("ASK", "Clarification requested", { missing: this.missing(this.active) });
          return this.result(t("reask", lang, { q }));
        }
        const jx = this.active, still = this.missing(jx);
        let ack;
        if (still.length) { const labels = filled.map(f => (FIELD_LABEL[f] || [f, f])[lang === "ar" ? 1 : 0]); ack = t("ack_partial", lang, { fields: labels.join(lang === "ar" ? " و" : " and ") }); }
        else ack = t("ack_complete", lang);
        const rr = this.advance(jx, lang);
        if (["collecting", "confirm"].includes(rr.mode)) rr.reply = ack + "\n\n" + rr.reply;
        return rr;
      }
      if (/\b(who|what) are you\b|\bare you (a |an )?(bot|ai|robot|chatgpt|gpt|human)\b|\bwhat model\b|\bwho (made|built) you\b|من انت|مين انت|وش انت/i.test(text)) {
        this.emit("INTENT", "Identity question", {}); return this.result(t("identity", lang)); }
      const res = this.classifyWithContext(text);
      if (res.intent === null) {
        if (/^\s*(hi|hello|hey|salam|السلام|مرحبا|هلا)(?![\p{L}\p{N}_])/iu.test(text)) return this.result(t("greeting", lang, { name: ((lang === "ar" && this.ctx.name_ar) || this.ctx.name).split(" ")[0] }));
        this.emit("INTENT", "No supported intent (out of scope)", { confidence: res.confidence, engine: res.engine });
        return this.result(t("fallback", lang));
      }
      if (res.intent === "case_status") return this.status(lang, res);
      if (res.intent === "report_stolen_vehicle" && res.concepts.includes("LICENCE")) {
        if (!res.secondary.includes("replace_lost_licence")) res.secondary.push("replace_lost_licence");
        this.carry = { replace_lost_licence: { loss_reason: "stolen" } };
      }
      for (const s of res.secondary) if (!this.queue.includes(s)) this.queue.push(s);
      return this.start(res.intent, text, lang, res);
    }

    setLanguage(lang) {
      if (!["ar", "en"].includes(lang)) return this.result("");
      const old = this.lang; this.lang = lang;
      this.emit("ADAPT", `Conversation language switched ${old || "-"} → ${lang} (state preserved)`, { mode: this.mode, active_case: this.active ? this.active.case_id : null });
      const j = this.active;
      if (this.mode === "collecting" && j) { const m = this.missing(j); return this.result(t("lang_switched", lang) + "\n\n" + this.question(j, m, lang), this.quick(j, m)); }
      if (this.mode === "confirm" && j) { const [r, q] = this.confirmMessage(j, lang); return this.result(t("lang_switched", lang) + "\n\n" + r, q); }
      if (this.mode === "stepup") return this.result(t("lang_switched", lang) + "\n\n" + t("stepup", lang, { otp: this.otp }), [this.otp]);
      return this.result(t(this.journeys.length ? "lang_switched" : "lang_switched_idle", lang));
    }

    tick(minutes = 15) {
      this.clock.advance(minutes); this.backends.advance();
      this.emit("MONITOR", `Monitoring sweep at t+${this.clock.now.toFixed(0)} min`, { open_cases: this.journeys.length });
      for (const j of this.journeys) {
        if (j.pending.length) this.resume(j);
        for (const [tool, ref] of Object.entries(j.refs)) {
          let st; try { st = this.client.call("case.get_status", { ref }, `status-${ref}-${this.clock.now}`, new Set()); }
          catch (e) { this.emit("ADAPT", "Status check failed", { ref, error: String(e.message) }); continue; }
          if (st.status !== j.last_status[ref]) {
            const old = j.last_status[ref] ?? null; j.last_status[ref] = st.status;
            this.emit("MONITOR", `${ref}: ${old} → ${st.status}`, { tool, ref });
            if (MEANINGFUL.has(st.status)) { const lg = this.lang || "en";
              this.notify(j, `${ref}:${st.status}`, t("n_status", lg, { title: j.policy.title[lg], status: statusText(st.status, lg), ref })); }
          }
        }
        this.verifyOutcome(j);
      }
      return this.result("");
    }

    configure({ inject, clear, policy_date, pin } = {}) {
      if (clear) { this.backends.clearFailures(); for (const b of Object.values(this.client.breakers)) { b.failures = 0; b.open_until = -1; } }
      for (const [tool, modes] of Object.entries(inject || {})) this.backends.inject(tool, modes);
      if (policy_date) this.policies.setDate(policy_date);
      for (const [i, v] of Object.entries(pin || {})) this.policies.pin(i, v);
    }

    // -------- understanding
    contextConcepts(text) {
      const tn = normalise(text), extra = new Set();
      if (this.ctx.commercial_registrations.some(c => tn.includes(normalise(c.name)))) extra.add("BUSINESS");
      if (this.ctx.vehicles.some(v => tn.includes(v.model.toLowerCase()) || tn.includes(v.plate.toLowerCase()))) extra.add("VEHICLE");
      return extra;
    }
    classifyWithContext(text) {
      const res = classify(text, this.contextConcepts(text));
      const p = this.llmProposal;
      if (p && p.intent !== undefined) {           // a real LLM's proposal: accepted only if it is in the closed set
        if (p.intent === null || D.intents.includes(p.intent)) {
          if (p.intent !== res.intent) this.emit("ADAPT", "LLM understanding differs from built-in model — LLM proposal used (validated)", { llm: p.intent, built_in: res.intent, model: p.model });
          res.intent = p.intent; res.engine = `llm:${p.model} (validated against closed intent set)`;
          if (p.intent === null) res.secondary = [];
        } else this.emit("GUARD", "LLM proposed a service that does not exist — ignored", { proposed: p.intent, model: p.model });
      }
      return res;
    }
    start(intent, text, lang, res, carryText = true) {
      res = res || classify(text);
      this.emit("INTENT", `Intent: ${intent}`, { confidence: round(res.confidence, 2), engine: res.engine, concepts: res.concepts, also_detected: res.secondary });
      const pol = this.policies.retrieve(intent);
      this.emit("POLICY", `Policy retrieved: ${pol.file} · ${pol.policy_id} v${pol.version}`, { file: pol.file, source_id: pol.source_id, effective: pol.effective, authority: pol.authority, risk: pol.risk, autonomy: pol.autonomy, summary: pol.summary });
      const j = { intent, policy: pol, case_id: `HC-2026-${String(this.caseSeq++).padStart(6, "0")}`, lang, slots: {}, plan: [], plan_hash: "", consent: null, refs: {}, last_status: {},
        pending: [], status: "collecting", escalation: null, outcome_verified: false, reask: 0, notified: new Set(), flags: {} };
      this.active = j;
      this.emit("CONTEXT", "Authenticated context loaded (minimised)", { name: this.ctx.name, national_id: maskId(this.ctx.national_id) });
      const idv = this.readTool(j, "identity.verify_identity", { nid: this.ctx.national_id });
      if (!(idv && idv.verified)) return this.ineligible(j, ["identity_verified"], lang);
      if (intent === "report_stolen_vehicle") this.readTool(j, "vehicle.get_user_vehicles", { nid: this.ctx.national_id });
      if (intent === "replace_lost_licence") this.readTool(j, "traffic.check_licence_status", { nid: this.ctx.national_id });
      if (["hire_employee", "end_employee_contract"].includes(intent)) {
        const crs = this.ctx.commercial_registrations;
        if (!crs.length) return this.ineligible(j, ["owns_any_cr"], lang);
        this.readTool(j, "commerce.verify_registration", { nid: this.ctx.national_id, cr: crs[0].cr });
      }
      const carried = this.carry[intent] || {}; delete this.carry[intent];
      for (const [k, v] of Object.entries(carried)) { j.slots[k] = v; this.emit("CONTEXT", "Carried over from earlier message", { [k]: v }); }
      if (carryText) this.fillSlots(j, text, false);
      const sad = ["report_stolen_vehicle", "replace_lost_licence"].includes(intent);
      const act = pol.action;
      const head = t("can_do", lang, { opener: act.opener[lang], action: act.name[lang], promise: act.promise[lang], pid: pol.policy_id, ver: pol.version });
      const r = this.advance(j, lang); r.reply = head + "\n\n" + r.reply; return r;
    }

    // -------- slots
    activeSlots(j) { return j.policy.slots.filter(s => !s.condition || CONDITIONS[s.condition](this.ctx, j.slots)); }
    missing(j) { return this.activeSlots(j).filter(s => s.required && !j.slots[s.name]).map(s => s.name); }
    options(slot) { return slot === "vehicle" ? this.ctx.vehicles : slot === "cr" ? this.ctx.commercial_registrations : []; }
    fillSlots(j, text, collecting) {
      const filled = [], missing = this.missing(j);
      const llmSlots = (this.llmProposal && this.llmProposal.slots) || {};
      for (const s of this.activeSlots(j)) {
        const name = s.name, typ = s.type; if (j.slots[name]) continue;
        let val = null;
        if (typ === "owned_vehicle") {
          const opts = this.options("vehicle");
          if (opts.length === 1) { val = opts[0].plate; this.emit("CONTEXT", "Only one registered vehicle — auto-selected", { plate: val }); }
          else { const i = matchOption(text, opts, ["plate", "model", "make", "colour", "model_ar", "make_ar"]); val = i !== null ? opts[i].plate : null; }
        } else if (typ === "owned_cr") {
          const opts = this.options("cr");
          if (opts.length === 1) val = opts[0].cr;
          else if (opts.length) {
            let i = matchOption(text, opts, ["cr", "name"]);
            if (i === null) { const tn = normalise(text); for (let k = 0; k < opts.length; k++) if (normalise(opts[k].name).split(" ").some(w => w.length > 4 && tn.includes(w))) { i = k; break; } }
            val = i !== null ? opts[i].cr : null;
          }
        } else if (typ === "time_expr") val = extractTime(text);
        else if (typ === "location") {
          val = extractLocation(text);
          if (!val && collecting && missing.length === 1 && missing[0] === name && text.split(/\s+/).length <= 8) val = stripChars(text, " .");
        } else if (typ.startsWith("enum:")) val = extractEnum(text, typ);
        else if (typ === "confirm") { if (collecting && missing.length && missing[0] === name) { const yn = yesNo(text); val = yn === null ? null : (yn ? "confirmed" : "declined"); } }
        else if (typ === "money") val = (collecting || /\d/.test(text)) ? extractMoney(text) : null;
        else if (typ === "free_text" && collecting) val = this.freeText(name, text, missing);
        else if (typ === "free_text" && name.endsWith("_reason")) {
          const m = text.match(/\b(?:because|since|as|due to)\b\s+(.{3,200})|(?:لأن|بسبب)\s*(.{3,200})/i);
          val = m ? stripChars(m[1] || m[2], " .") : null;
        }
        if (!val && llmSlots[name] != null) val = this.validateLlmSlot(s, llmSlots[name]);
        if (val) { j.slots[name] = val; filled.push(name); }
      }
      if (collecting && missing.includes("incident_location") && !filled.includes("incident_location") && text.split(/\s+/).length <= 6
          && yesNo(text) === null && !screenInput(text).length && PLACE_RE.test(normalise(text))) {
        const parts = stripChars(text, " .").split(/\s*(?:،|,)\s*/);
        j.slots.incident_location = parts.find(x => PLACE_RE.test(normalise(x))) || stripChars(text, " ."); filled.push("incident_location");
      }
      if (filled.length) { const d = {}; for (const k of filled) d[k] = j.slots[k]; this.emit("SLOTS", "Information extracted (Planning Agent state updated)", d); }
      return filled;
    }
    validateLlmSlot(slot, raw) {
      // The LLM may suggest a value the rules missed; it is only used if it passes the same type checks.
      const v = String(raw).trim(); let ok = null;
      if (slot.type === "owned_vehicle") { const veh = this.ctx.vehicles.find(x => x.plate === v || x.model.toLowerCase() === v.toLowerCase()); ok = veh ? veh.plate : null; }
      else if (slot.type === "owned_cr") { const c = this.ctx.commercial_registrations.find(x => x.cr === v || x.name.toLowerCase() === v.toLowerCase()); ok = c ? c.cr : null; }
      else if (slot.type.startsWith("enum:")) ok = slot.type.slice(5).split("|").includes(v.toLowerCase()) ? v.toLowerCase() : null;
      else if (slot.type === "money") ok = extractMoney(v);
      else if (slot.type === "confirm") ok = null;   // confirmations must come from the citizen, never the model
      else ok = (v.length >= 2 && v.length <= 120 && !screenInput(v).length) ? v : null;
      this.emit(ok ? "SLOTS" : "GUARD", ok ? "LLM-extracted value accepted after validation" : "LLM-extracted value rejected by validation", { slot: slot.name, value: v });
      return ok;
    }
    freeText(name, text, missing) {
      const txt = stripChars(text, " .");
      if (!missing.includes(name)) return null;
      if (name === "job_title") {
        const m = txt.match(/\b(?:as\s+(?:an?\s+)?|an?\s+)([a-z][a-z \-]{2,40}?)(?=\s+(?:for|at|with|on|paying|earning)\b|[,.]|$)/i);
        if (m) return m[1].trim();
        const t2 = stripChars(txt.replace(/\d[\d,.]*\s*(k|sar|riyals?|ريال)?/gi, ""), " ,.");
        return t2 && missing[0] === name ? t2 : null;
      }
      if (name === "employee") { const m = txt.match(/^\s*([A-Z][a-z]+(?: [A-Z][a-z]+)?)/); if (m) return m[1]; return missing[0] === name ? txt.split(",")[0].slice(0, 60) : null; }
      if (["termination_reason", "closure_reason"].includes(name)) {
        if (missing.length > 1 && missing[0] !== name) {
          const m = txt.match(/,|\bbecause\b|\bas\b|\bsince\b|لأن|بسبب/);
          if (!m) return null; const rest = txt.slice(m.index + m[0].length).trim(); return rest || null;
        }
        return txt.slice(0, 200);
      }
      return missing[0] === name ? txt.slice(0, 120) : null;
    }
    question(j, missing, lang) {
      const qs = [];
      for (const name of missing) {
        if (name === "vehicle" || name === "cr") {
          const opts = this.options(name); let o;
          if (name === "vehicle" && lang === "ar") o = opts.map((v, i) => `\n   ${i + 1}) ${v.make_ar || v.make} ${v.model_ar || v.model} ${v.year} · ${v.plate}`).join("");
          else if (name === "vehicle") o = opts.map((v, i) => `\n   ${i + 1}) ${titleCase(v.colour)} ${v.make} ${v.model} ${v.year} · ${v.plate}`).join("");
          else o = opts.map((c, i) => `\n   ${i + 1}) ${c.name} · CR ${c.cr}`).join("");
          qs.push(t(`slot.${name}`, lang, { options: o }));
        } else if (name === "photo_update") qs.push(t("slot.photo_update", lang, { ver: j.policy.version, year: this.ctx.licence.photo_year }));
        else qs.push(t(`slot.${name}`, lang));
      }
      return qs.length === 1 ? qs[0] : qs.map(q => `• ${q}`).join("\n");
    }

    // -------- plan / verify
    advance(j, lang) {
      if (j.slots.vehicle && this.backends.reportedPlates.has(j.slots.vehicle) && j.policy.eligibility.includes("vehicle_not_already_reported"))
        return this.ineligible(j, ["vehicle_not_already_reported"], lang);
      const missing = this.missing(j);
      if (missing.length) {
        this.mode = "collecting";
        this.emit("ASK", "Asking only for missing information", { missing, already_known: Object.keys(j.slots).concat(["identity", "contact", "address"]) });
        return this.result((missing.length > 1 ? t("ask_intro", lang) + "\n" : "") + this.question(j, missing, lang), this.quick(j, missing));
      }
      if (j.slots.photo_update === "declined") return this.ineligible(j, [], lang, "A current photo is required by policy v3.1.");
      if (j.slots.vehicle) this.readTool(j, "vehicle.verify_vehicle", { nid: this.ctx.national_id, plate: j.slots.vehicle });
      if (j.slots.cr) this.readTool(j, "commerce.verify_registration", { nid: this.ctx.national_id, cr: j.slots.cr });
      if (j.intent === "replace_lost_licence" && j.slots.delivery_method === "pickup" && !j.slots.appointment) {
        const sl = this.readTool(j, "traffic.find_branch_slot", { nid: this.ctx.national_id });
        if (sl) j.slots.appointment = `${sl.slot} · ${sl.branch}`;
      }
      if (j.intent === "hire_employee" && j.slots.employee && this.ctx.commercial_registrations.length) {
        const cr = this.ctx.commercial_registrations[0].cr;
        this.readTool(j, "hr.verify_employee", { employer_cr: cr, employee: j.slots.employee });
        this.readTool(j, "hr.check_existing_contract", { employer_cr: cr, employee: j.slots.employee });
      }
      const fails = [], checks = {};
      for (const rule of j.policy.eligibility) { const ok = !!RULES[rule](this.ctx, j.slots, this.backends); checks[rule] = ok ? "pass" : "FAIL"; if (!ok) fails.push(rule); }
      this.emit("VERIFY", "Eligibility checked against policy rules", Object.assign({ policy: `${j.policy.policy_id} v${j.policy.version}` }, checks));
      if (fails.length) return this.ineligible(j, fails, lang);
      j.plan = this.buildPlan(j); j.plan_hash = planHash(j.case_id, j.plan);
      this.emit("PLAN", `Plan built: ${j.plan.length} step(s)`, { steps: j.plan.map(s => `${s.id}: ${s.tool}`), autonomy: j.policy.autonomy, plan_hash: j.plan_hash, consent_required: true, step_up: !!j.policy.step_up_auth });
      const guardEvents = this.audit.entries.filter(e => e.kind === "GUARD").length;
      const rv = securityReview(j.plan, j.policy, j.intent, this.session, this.ctx, j.slots, this.backends, guardEvents);
      j.flags.security = rv.decision;
      const sd = { risk: rv.risk, requires: rv.requires }; for (const c of rv.checks) sd[c.check] = c.result;
      this.emit("SECURITY", `Security Agent: ${rv.decision} — ${rv.reason}`, sd);
      if (rv.decision === "BLOCK" || (rv.decision === "ESCALATE" && j.policy.autonomy !== "human_review")) {
        this.mode = "idle"; j.status = rv.decision === "BLOCK" ? "blocked_by_security" : "escalated"; j.escalation = this.ticket();
        this.emit("ESCALATE", "Stopped by Security Agent — no action taken", { ticket: j.escalation, decision: rv.decision });
        return this.result(t(rv.decision === "BLOCK" ? "security_block" : "security_escalate", lang, { reason: rv.reason, ticket: j.escalation }));
      }
      this.mode = "confirm"; j.status = "awaiting_consent";
      this.emit("CONSENT", "Human approval gate — waiting for explicit confirmation", { plan_hash: j.plan_hash });
      const [reply, quick] = this.confirmMessage(j, lang); return this.result(reply, quick);
    }
    readTool(j, tool, args) {
      let res; try { res = this.client.call(tool, args, `ro-${tool}-${j.case_id}`, new Set()); }
      catch (e) { this.emit("ADAPT", `Verification tool ${tool} failed`, { error: String(e.message) }); return null; }
      let title;
      if (tool === "identity.verify_identity") { j.flags.identity_verified = !!res.verified; title = res.verified ? "Identity verified" : "Identity NOT verified"; }
      else if (tool === "vehicle.get_user_vehicles") title = `Registered vehicles retrieved (${res.vehicles.length})`;
      else if (tool === "vehicle.verify_vehicle") { j.flags.ownership_verified = !!res.owner_match; title = res.owner_match ? "Vehicle ownership verified" : "Vehicle ownership NOT verified"; }
      else if (tool === "traffic.check_licence_status") title = `Licence status checked: ${res.licence_status}`;
      else if (tool === "traffic.find_branch_slot") title = `Appointment arranged: ${res.slot}`;
      else if (tool === "hr.verify_employee") title = "Employee verified";
      else if (tool === "hr.check_existing_contract") title = !res.existing_contract ? "Existing contract checked: none found" : "Existing contract found";
      else { j.flags.ownership_verified = !!res.owner_match; title = res.owner_match ? "Business ownership verified" : "Business ownership NOT verified"; }
      this.emit("VERIFY", `${title} — tool ${tool} (mock)`, Object.assign({ tool }, res));
      return res;
    }
    confirmMessage(j, lang) {
      const steps = j.plan.map((s, i) => `${i + 1}. ${s.label[lang]}`).join("\n"); let note = "";
      if (j.policy.fee_sar) note += t("note_fee", lang, { fee: j.policy.fee_sar });
      if (j.policy.autonomy === "assisted") note += t("note_assisted", lang);
      if (j.policy.autonomy === "human_review") note += t("note_human", lang);
      const yn = [j.policy.action.confirm[lang], lang === "ar" ? "إلغاء" : "Cancel"];
      return [t("confirm", lang, { steps, details: this.details(j, lang), autonomy_note: note }), yn];
    }
    details(j, lang = "en") {
      const parts = [];
      for (let [k, v] of Object.entries(j.slots)) {
        if (k === "vehicle") { const veh = this.ctx.vehicles.find(x => x.plate === v); v = lang === "ar" ? `${veh.make_ar || veh.make} ${veh.model_ar || veh.model} (${v})` : `${veh.colour} ${veh.make} ${veh.model} (${v})`; }
        else if (k === "cr") { const c = this.ctx.commercial_registrations.find(x => x.cr === v); v = `${c.name} (CR ${v})`; }
        else if (lang === "ar") v = D.value_ar[v] || v;
        const label = k.replace(/_/g, " ");
        parts.push(`${lang === "ar" ? (D.detail_ar[label] || label) : label}: ${v}`);
      }
      return parts.join(lang === "ar" ? "؛ " : "; ");
    }
    quick(j, missing) {
      const m = missing[0], ar = this.lang === "ar";
      if (m === "vehicle") return this.ctx.vehicles.map(v => ar ? `ال${v.model_ar || v.model}` : `The ${v.model}`);
      if (m === "cr") return this.ctx.commercial_registrations.map(c => c.name);
      if (m === "loss_reason") return ar ? ["ضائعة", "مسروقة", "تالفة"] : ["Lost", "Stolen", "Damaged"];
      if (m === "delivery_method") return ar ? ["توصيل لعنواني", "استلام من الفرع"] : ["Deliver to my address", "Pick up from branch"];
      if (m === "photo_update") return ar ? ["نعم", "لا"] : ["Yes", "No"];
      return [];
    }
    buildPlan(j) {
      const c = this.ctx, s = j.slots, plan = [];
      for (const step of j.policy.steps) {
        if (step.condition && !CONDITIONS[step.condition](c, s)) continue;
        const A = {
          "police.file_theft_report": () => ({ owner_nid: c.national_id, plate: s.vehicle, incident_time: s.incident_time, incident_location: s.incident_location }),
          "registry.flag_vehicle": () => ({ plate: s.vehicle, police_ref: "$s1.ref" }),
          "insurer.notify_claim": () => ({ plate: s.vehicle, police_ref: "$s1.ref", insurer: c.vehicles.find(v => v.plate === s.vehicle).insurer }),
          "payment.charge_fee": () => ({ payer_nid: c.national_id, amount_sar: String(j.policy.fee_sar), purpose: "LICENCE_REPLACEMENT" }),
          "traffic.issue_replacement": () => ({ holder_nid: c.national_id, licence_no: c.licence.number, reason: s.loss_reason, delivery: s.delivery_method, payment_ref: "$s1.ref" }),
          "commerce.cancel_cr": () => ({ owner_nid: c.national_id, cr: s.cr, reason: s.closure_reason }),
          "hr.create_contract": () => ({ employer_cr: c.commercial_registrations[0].cr, employee: s.employee, job_title: s.job_title, salary: s.monthly_salary }),
          "hr.route_to_officer": () => ({ employer_nid: c.national_id, employee: s.employee, reason: s.termination_reason }),
          "hr.draft_resignation": () => ({ worker_nid: c.national_id, employer: c.employer, last_day: s.last_working_day }),
        };
        plan.push(Object.assign({}, step, { args: A[step.tool]() }));
      }
      return plan;
    }
    ineligible(j, fails, lang, custom) {
      this.mode = "idle"; j.status = "ineligible";
      if (fails.includes("vehicle_not_already_reported")) {
        const ex = this.journeys.find(x => x.slots.vehicle === j.slots.vehicle); const existing = ex ? ex.case_id : "—";
        this.emit("ADAPT", "Duplicate report prevented — existing case found", { plate: j.slots.vehicle, existing_case: existing });
        return this.result(t("already_reported", lang, { case: existing }), [lang === "ar" ? "تحقق من الحالة" : "Check status"]);
      }
      let tk = null, nxt;
      if (fails.includes("identity_verified")) { tk = this.ticket(); nxt = t("ineligible_next_identity", lang, { ticket: tk }); }
      else if (j.intent === "close_business") { nxt = t("ineligible_next_cr", lang); delete j.slots.cr; this.active = j; this.mode = "collecting"; }
      else { tk = this.ticket(); nxt = t("ineligible_next_generic", lang, { ticket: tk }); }
      const lines = fails.map(f => `✗ ${D.rule_labels[f][lang]}`).join("\n") || (custom || "");
      this.emit(tk ? "ESCALATE" : "ADAPT", "Not eligible — no action taken", { failed_rules: fails, ticket: tk });
      if (tk) j.escalation = tk;
      return this.result(t("ineligible", lang, { fails: lines, next: nxt }));
    }

    // -------- consent / act
    onConfirm(text, lang) {
      const j = this.active, yn = yesNo(text);
      if (yn === null) { const res = classify(text);
        if (res.intent && res.intent !== j.intent && res.confidence >= 0.6) { j.status = "parked"; this.mode = "idle"; return this.start(res.intent, text, lang); }
        return this.result(t("confirm_reask", lang)); }
      if (!yn) return this.cancel(lang);
      const cur = this.policies.retrieve(j.intent);
      if (cur.version !== j.policy.version) {
        const old = j.policy.version;
        this.emit("ADAPT", `Policy changed v${old} → v${cur.version} — re-planning`, { source_id: cur.source_id, effective: cur.effective });
        j.policy = cur; const r = this.advance(j, lang); r.reply = t("policy_changed", lang, { old, new: cur.version }) + "\n\n" + r.reply; return r;
      }
      j.consent = issueConsent(j.case_id, j.plan_hash);
      this.emit("CONSENT", "Explicit consent captured", { bound_to_plan: j.plan_hash });
      if (j.policy.step_up_auth) {
        this.otp = String(Math.floor(this.rng() * 1000000)).padStart(6, "0"); this.otpAttempts = 0; this.mode = "stepup";
        this.emit("AUTH", "Step-up authentication requested (high-risk action)", { method: "OTP to registered phone (mock)" });
        return this.result(t("stepup", lang, { otp: this.otp }), [this.otp]);
      }
      return this.execute(j, lang);
    }
    onStepup(text, lang) {
      const code = text.replace(/\D/g, "");
      if (yesNo(text) === false) return this.cancel(lang);
      if (code === this.otp) { this.emit("AUTH", "Step-up authentication passed", {}); this.otp = null; return this.execute(this.active, lang); }
      this.otpAttempts += 1; this.emit("AUTH", "Step-up code rejected", { attempt: this.otpAttempts });
      if (this.otpAttempts >= 3) { this.mode = "idle"; this.active.status = "cancelled"; this.otp = null; return this.result(t("stepup_locked", lang)); }
      return this.result(t("stepup_fail", lang));
    }
    cancel(lang) {
      if (this.active) { this.active.status = "cancelled"; this.emit("CANCEL", "Journey cancelled by citizen — nothing submitted", { case: this.active.case_id }); }
      this.mode = "idle"; this.active = null; return this.result(t("cancelled", lang));
    }
    idem(j, step) { return sha256(`${j.case_id}:${step.id}:${j.plan_hash}`).slice(0, 16); }
    resolve(args, j) { const out = {}; for (const [k, v] of Object.entries(args)) {
      if (typeof v === "string" && v.startsWith("$")) { const sid = v.slice(1).split(".")[0]; const tool = j.plan.find(s => s.id === sid).tool; out[k] = j.refs[tool] ?? null; }
      else out[k] = v; } return out; }
    runStep(j, step) {
      if (!verifyConsent(j.consent, j.case_id, j.plan_hash)) { this.emit("GUARD", "Consent token invalid — action blocked", { tool: step.tool }); return "blocked"; }
      const args = this.resolve(step.args, j);
      const [aok, acode, awhy] = authorize(step.tool, args, this.session.nid, this.ctx);
      this.emit("AUTHZ", `Deterministic authorization: ${aok ? "ALLOW" : "DENY " + acode} — ${awhy}`, { tool: step.tool, session: maskId(this.session.nid), decision: aok ? "ALLOW" : "DENY" });
      if (!aok) return "blocked";
      const idem = this.idem(j, step), tools = new Set(j.policy.steps.map(s => s.tool));
      const onEvent = (kind, d) => { if (kind === "attempt_failed") this.emit("ADAPT", `${d.tool} attempt ${d.attempt} failed — ` + (d.next_backoff_s ? `retrying in ${d.next_backoff_s}s with same idempotency key` : "giving up"), d); };
      let res;
      try { res = this.client.call(step.tool, args, idem, tools, onEvent); }
      catch (e) {
        if (e instanceof GuardrailViolation) { this.emit("GUARD", `Tool call rejected by gateway: ${e.code}`, { tool: step.tool, detail: e.detail }); return "blocked"; }
        if (e instanceof ToolRejected) { this.emit("ADAPT", "Business rejection — not retried", { tool: step.tool, reason: e.reason }); return `rejected:${e.reason}`; }
        if (e instanceof CircuitOpen || e instanceof ToolUnavailable) { this.emit("ADAPT", "Service unavailable — circuit breaker / safe fallback", { tool: step.tool, error: e.message }); return "unavailable"; }
        throw e;
      }
      j.refs[step.tool] = res.ref; j.last_status[res.ref] = res.status;
      this.emit("ACT", `Authorised tool executed: ${step.tool} → ${res.ref}`, { args, status: res.status, idempotency_key: idem, duplicate: !!res.duplicate,
        policy: `${j.policy.policy_id} v${j.policy.version}`, plan_hash: j.plan_hash, reason: step.label.en });
      return "ok";
    }
    execute(j, lang) {
      this.mode = "idle"; j.status = "executing"; if (!this.journeys.includes(j)) this.journeys.push(j);
      const results = []; let issue = null;
      for (const step of j.plan) {
        if ((step.depends_on || []).some(d => j.pending.map(p => p.id).includes(d))) { j.pending.push(step); continue; }
        const out = this.runStep(j, step);
        if (out === "ok") results.push(`✓ ${step.label[lang]} — ${j.refs[step.tool]}`);
        else if (out === "unavailable") {
          j.pending.push(step);
          if (!j.escalation) { j.escalation = this.ticket(); this.emit("ESCALATE", "Queued for auto-resume + officer notified", { ticket: j.escalation, step: step.tool }); }
          issue = t("recovery", lang, { service: step.tool.split(".")[0], ticket: j.escalation });
          this.notify(j, "recovery", t("n_recovery", lang, { title: j.policy.title[lang], ticket: j.escalation }), "warn");
        } else if (out.startsWith("rejected")) {
          const why = out.split(":").slice(1).join(":");
          if (step.optional) { results.push(`– ${step.label[lang]} skipped (${why})`); continue; }
          j.escalation = this.ticket(); issue = t("rejected", lang, { reason: why, ticket: j.escalation }); j.status = "rejected";
          this.emit("ESCALATE", "Escalated after rejection", { ticket: j.escalation }); break;
        } else { issue = t("blocked", lang); j.status = "blocked"; break; }
      }
      if (j.status === "executing") j.status = j.pending.length ? "pending_recovery" : "tracking";
      this.emit("MONITOR", "Case under monitoring", { case: j.case_id, refs: Object.assign({}, j.refs), pending: j.pending.map(p => p.tool) });
      if (results.length) this.notify(j, "submitted", t("n_submitted", lang, { title: j.policy.title[lang], case: j.case_id }));
      this.active = null;
      const resTxt = results.join("\n") || (lang === "ar" ? "لم يُنفذ أي إجراء بعد." : "Nothing has been submitted yet.");
      let reply = issue ? t("done_partial", lang, { results: resTxt, case: j.case_id, issue }) : t("done", lang, { results: resTxt, case: j.case_id });
      let quick = [];
      if (this.queue.length) { reply += "\n\n" + t("queued_next", lang, { title: this.policies.retrieve(this.queue[0]).title[lang] }); this.mode = "offer_next"; quick = ["Yes", "Not now"]; }
      return this.result(reply, quick);
    }
    resume(j) {
      const still = [];
      for (const step of j.pending) {
        const deps = step.depends_on || [];
        if (deps.some(d => !(j.plan.find(s => s.id === d).tool in j.refs))) { still.push(step); continue; }
        const out = this.runStep(j, step);
        if (out === "ok") { this.emit("ADAPT", `Resumed: ${step.tool} completed (original idempotency key)`, { ref: j.refs[step.tool] });
          const lg = this.lang || "en"; this.notify(j, `resumed:${step.id}`, t("n_resumed", lg, { title: j.policy.title[lg], step: step.label[lg], ref: j.refs[step.tool] })); }
        else if (out.startsWith("rejected") && step.optional) continue;
        else still.push(step);
      }
      j.pending = still; if (!still.length && j.status === "pending_recovery") j.status = "tracking";
    }
    verifyOutcome(j) {
      if (j.outcome_verified || j.status !== "tracking") return;
      const target = j.policy.target_state;
      const ok = Object.entries(target).every(([tool, st]) => tool in j.refs && statusRank(tool, j.last_status[j.refs[tool]] || "") >= statusRank(tool, st));
      if (ok) { j.outcome_verified = true; j.status = "completed"; const obs = {}; for (const tool of Object.keys(target)) obs[tool] = j.last_status[j.refs[tool]];
        this.emit("OUTCOME", "Outcome verified against policy target state", { target, observed: obs });
        const lg = this.lang || "en"; this.notify(j, "completed", t("n_completed", lg, { title: j.policy.title[lg], case: j.case_id }), "success"); }
    }
    status(lang, res) {
      this.emit("INTENT", "Intent: case_status", { confidence: round(res.confidence, 2) });
      if (!this.journeys.length) return this.result(t("status_none", lang));
      const lines = this.journeys.map(j => { const sts = Object.values(j.refs).map(r => statusText(j.last_status[r] || "", lang)).join(lang === "ar" ? "، " : ", ");
        return t("status_line", lang, { title: j.policy.title[lang], case: j.case_id, status: (sts || j.status) + (j.outcome_verified ? " ✓" : "") }); });
      return this.result(lines.join("\n"));
    }
    casesSummary() {
      return this.journeys.map(j => { const sl = {}, sts = {}; for (const r of Object.values(j.refs)) { sl[r] = { en: statusText(j.last_status[r] || "", "en"), ar: statusText(j.last_status[r] || "", "ar") }; sts[r] = j.last_status[r] ?? null; }
        return { case: j.case_id, title: j.policy.title.en, title_ar: j.policy.title.ar, status_labels: sl, intent: j.intent, status: j.status,
          policy: `${j.policy.policy_id} v${j.policy.version}`, refs: Object.assign({}, j.refs), statuses: sts, escalation: j.escalation,
          outcome_verified: j.outcome_verified, pending: j.pending.map(p => p.tool) }; });
    }
    // helpers the page uses (same data the Python server returns)
    personaCard() { const c = this.ctx; return { name: c.name, name_ar: c.name_ar || c.name, national_id: maskId(c.national_id), identity_verified: c.identity_verified,
      vehicles: c.vehicles.map(v => `${v.make} ${v.model} · ${v.plate}`), licence: `${c.licence.status} (photo ${c.licence.photo_year})`,
      businesses: c.commercial_registrations.map(b => b.name), persona: this.persona }; }
    guardStats() { const titles = this.audit.entries.map(e => e.data.title || ""), kinds = this.audit.entries.map(e => e.kind);
      return { injections_blocked: titles.filter(x => x.includes("injection")).length, gateway_rejections: titles.filter(x => x.includes("rejected by gateway")).length,
        consent_gates: titles.filter(x => x.startsWith("Explicit consent")).length, step_up: titles.filter(x => x.includes("Step-up authentication passed")).length,
        escalations: kinds.filter(k => k === "ESCALATE").length, audit_entries: this.audit.entries.length, audit_chain_valid: this.audit.verify(),
        policy_date: this.policies.today, failures: Object.assign({}, this.backends.failures),
        breakers_open: Object.entries(this.client.breakers).filter(([, b]) => b.open_until > this.clock.now).map(([k]) => k) }; }
  }

  const api = { Agent, nlu, guardrails, securityReview, lastSeenDate, PolicyStore, sha256, hmac, data: D, version: "0.9.0-browser" };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.HUMAIN = api;
})(typeof window !== "undefined" ? window : globalThis);
