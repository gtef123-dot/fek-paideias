// Cloudflare Worker — proxy for Level-2 AI synthesis.
// Holds the Gemini key as a SECRET (env.GEMINI_API_KEY); the static site never
// sees it. Validates config, restricts by Origin, rate-limits per IP, and exposes
// a GET /health endpoint (no secret). See .env.example for the variables.
const MODEL = "gemini-2.5-flash";
const GEMINI = (key) =>
  `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent?key=${key}`;
const DEV_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"];
const WINDOW_MS = 60_000;
const DEFAULT_MAX_PER_MINUTE = 12;
const buckets = globalThis.__fekRateBuckets || (globalThis.__fekRateBuckets = new Map());

function rateLimited(req, maxPerMinute) {
  const ip = req.headers.get("CF-Connecting-IP") || req.headers.get("X-Forwarded-For") || "unknown";
  const now = Date.now();
  const b = buckets.get(ip) || { start: now, count: 0 };
  if (now - b.start > WINDOW_MS) { b.start = now; b.count = 0; }
  b.count += 1;
  buckets.set(ip, b);
  if (buckets.size > 500) {
    for (const [k, v] of buckets) if (now - v.start > WINDOW_MS) buckets.delete(k);
  }
  return b.count > maxPerMinute;
}

export default {
  async fetch(req, env) {
    const environment = (env.ENVIRONMENT || "production").toLowerCase();
    const isDev = environment === "development";
    const configured = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
    const allowed = isDev ? [...configured, ...DEV_ORIGINS] : configured;
    const origin = req.headers.get("Origin") || "";
    const originAllowed = !!origin && allowed.includes(origin);
    const cors = {
      "Access-Control-Allow-Origin": originAllowed ? origin : (allowed[0] || ""),
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Vary": "Origin",
    };
    const json = (obj, status = 200) =>
      new Response(JSON.stringify(obj), { status, headers: { ...cors, "Content-Type": "application/json" } });

    if (req.method === "OPTIONS") return new Response(null, { headers: cors });

    // Healthcheck — never exposes the key, only whether config is present.
    if (req.method === "GET") {
      return json({
        status: "ok",
        environment,
        allowed_origins_configured: configured.length > 0,
        gemini_key_configured: !!env.GEMINI_API_KEY,
      });
    }

    if (req.method !== "POST") return json({ error_code: "METHOD_NOT_ALLOWED", error: "Use POST" }, 405);

    // ── Config validation (clear errors instead of an opaque 500) ──
    if (!isDev && configured.length === 0) {
      return json({ error_code: "SERVER_CONFIG_ERROR", error: "Missing ALLOWED_ORIGINS" }, 500);
    }
    if (!env.GEMINI_API_KEY) {
      return json({ error_code: "SERVER_CONFIG_ERROR", error: "Missing GEMINI_API_KEY" }, 500);
    }
    if (!originAllowed) {
      return json({ error_code: "FORBIDDEN_ORIGIN", error: "Origin not allowed" }, 403);
    }

    const maxPerMinute = Number(env.MAX_REQUESTS_PER_MINUTE || DEFAULT_MAX_PER_MINUTE);
    if (rateLimited(req, maxPerMinute)) return json({ error_code: "RATE_LIMITED", error: "Too many requests" }, 429);

    let body;
    try { body = await req.json(); } catch { return json({ error_code: "BAD_REQUEST", error: "Invalid JSON" }, 400); }
    const prompt = String(body && body.prompt || "").slice(0, 24000);
    if (!prompt) return json({ error_code: "BAD_REQUEST", error: "Empty prompt" }, 400);

    const g = await fetch(GEMINI(env.GEMINI_API_KEY), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contents: [{ parts: [{ text: prompt }] }],
        generationConfig: { temperature: 0, responseMimeType: "application/json" },
      }),
    });
    const data = await g.json().catch(() => ({}));
    const text = data?.candidates?.[0]?.content?.parts?.[0]?.text || "";
    return json({ text, upstream: g.status }, g.ok ? 200 : g.status);
  },
};
