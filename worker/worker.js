// Comunio-Live-Abruf für die Comunio Zentrale (Cloudflare Worker).
// Liest Livepunkte bei Comunio (nur lesend) und gibt sie mit dem Seitenschlüssel verschlüsselt zurück.
// Secrets: COMUNIO_USER, COMUNIO_PASS, PAGE_KEY (Base64, gleicher Schlüssel wie die verschlüsselte Seite).
const BASE = "https://www.comunio.de";
const UA = "Mozilla/5.0 (comunio-dashboard; private read-only)";
const ORIGIN = "https://mxmerkel-stack.github.io";
let tokenCache = null; // überlebt mehrere Aufrufe derselben Instanz

const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
const toB64 = u => btoa(String.fromCharCode(...new Uint8Array(u)));
const hex = u => [...new Uint8Array(u)].map(b => b.toString(16).padStart(2, "0")).join("");
const D = x => (x && typeof x === "object" && !Array.isArray(x)) ? x : {};
const num = x => { const n = Number(x); return x === null || x === undefined || x === "" || Number.isNaN(n) ? null : n; };

function cors(extra = {}) {
  return { "Access-Control-Allow-Origin": ORIGIN, "Access-Control-Allow-Headers": "X-Access",
           "Access-Control-Allow-Methods": "GET, OPTIONS", "Cache-Control": "no-store", ...extra };
}

async function login(env, force = false) {
  if (!force && tokenCache && tokenCache.until > Date.now()) return tokenCache.tok;
  const r = await fetch(BASE + "/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json", "User-Agent": UA },
    body: new URLSearchParams({ username: env.COMUNIO_USER, password: (env.COMUNIO_PASS || "").trim(), tzoffset: "1" }),
  });
  if (!r.ok) throw new Error("Comunio-Login fehlgeschlagen (" + r.status + ")");
  const tok = (await r.json()).access_token;
  if (!tok) throw new Error("Comunio-Login ohne Token");
  tokenCache = { tok, until: Date.now() + 20 * 60 * 1000 };
  return tok;
}

async function api(env, path, tok) {
  const r = await fetch(BASE + path, { headers: { Authorization: "Bearer " + tok, Accept: "application/json", "User-Agent": UA } });
  if (r.status === 401) { tokenCache = null; throw new Error("401"); }
  if (!r.ok) throw new Error(`HTTP ${r.status} ${path.split("?")[0].replace(/\d{4,}/g, "…")}`);
  return r.json();
}

async function points(env, tok) {
  const root = await api(env, "/api/", tok);
  const links = Object.fromEntries(Object.entries(root._links || {}).map(([k, v]) => [k, String(v.href).replace(BASE, "")]));
  const cid = root.community.id, me = root.user.id;
  const cur = await api(env, "/api/matchdays/current", tok);
  const live = await api(env, `/api/communities/${cid}/standings?period=live`, tok);
  const tmpl = links["game:lineup:matchday"] || `/api/communities/${cid}/users/${me}/lineup/matchday/:matchdayId/:ownerId`;
  const mdIds = [cur.id, D(cur.eventInfo).id].filter(Boolean);
  const managers = (live.items || []).map(it => {
    const u = D(D(it._embedded).user);
    return { id: String(u.id), name: u.name, livePoints: it.livePoints, total: it.totalPoints, scored: it.playersPossiblyScoredAmount, players: [] };
  });
  await Promise.all(managers.map(async m => {
    for (const mid of mdIds) {
      try {
        const lu = await api(env, tmpl.replace(":matchdayId", mid).replace(":ownerId", m.id), tok);
        m.tactic = lu.tactic;
        const slots = D(D(lu.items).lineup);
        m.players = Object.entries(slots).filter(([, p]) => p && p.id)
          .sort((a, b) => Number(a[0]) - Number(b[0]))
          .map(([slot, p]) => ({ id: String(p.id), name: p.name, club: D(p.club).name, slot: Number(slot), type: p.type,
                                livePoints: num(p.livePoints), matchStatus: p.matchStatus, status: p.status, subbed: p.wasLiveSubstituted }));
        return;
      } catch (e) { m.error = String(e.message || e); }
    }
  }));
  const fixtures = (cur.items || []).map(x => ({ home: D(x.home).name, away: D(x.guest).name, goalsHome: D(x.home).goals,
                                               goalsAway: D(x.guest).goals, kickoff: x.kickoff, started: x.started }));
  return { fetchedAt: new Date().toISOString(), matchday: cur.matchdayKey, started: cur.started, finished: cur.finished,
           fixtures, managers, me: String(me), source: "worker" };
}

async function stats(env, tok, ids, mdKey) {
  const out = {};
  await Promise.all(ids.slice(0, 40).map(async pid => {
    try {
      const h = await api(env, `/api/players/${pid}/match-statistics-history`, tok);
      const e = (h.matchStatisticsCollection || []).find(e => D(e.matchday).seasonEnd === 9999999 && String(D(e.matchday).key) === String(mdKey));
      out[pid] = { goals: num(e && e.goals) || 0, yellow: num(e && e.yellow) || 0, red: num(e && e.red) || 0, yellowRed: num(e && e.yellowRed) || 0 };
    } catch (_) { out[pid] = { goals: 0, yellow: 0, red: 0, yellowRed: 0 }; }
  }));
  return { stats: out };
}

async function encrypt(env, obj) {
  const key = await crypto.subtle.importKey("raw", b64(env.PAGE_KEY), "AES-GCM", false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(JSON.stringify(obj)));
  return { iv: toB64(iv), data: toB64(data) };
}

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: cors() });
    const expected = hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode("comunio-live:" + env.PAGE_KEY)));
    if (request.headers.get("X-Access") !== expected) return new Response("forbidden", { status: 403, headers: cors() });
    const url = new URL(request.url);
    try {
      let tok = await login(env);
      const run = async () => url.searchParams.get("part") === "stats"
        ? stats(env, tok, (url.searchParams.get("ids") || "").split(",").filter(x => /^\d+$/.test(x)), url.searchParams.get("md"))
        : points(env, tok);
      let res;
      try { res = await run(); } catch (e) { if (String(e.message) !== "401") throw e; tok = await login(env, true); res = await run(); }
      return new Response(JSON.stringify(await encrypt(env, res)), { headers: cors({ "Content-Type": "application/json" }) });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e.message || e) }), { status: 502, headers: cors({ "Content-Type": "application/json" }) });
    }
  },
};
