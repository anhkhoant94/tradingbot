function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function ictStamp(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date).reduce((out, part) => {
    out[part.type] = part.value;
    return out;
  }, {});
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

async function triggerForecast(env, meta = {}) {
  if (!env.TRIGGER_URL) {
    return { ok: false, reason: "MISSING_TRIGGER_URL" };
  }
  if (!env.EZ_TRIGGER_SECRET) {
    return { ok: false, reason: "MISSING_EZ_TRIGGER_SECRET" };
  }

  const url = new URL(env.TRIGGER_URL);
  url.searchParams.set("source", meta.source || "cloudflare-cron");
  if (meta.cron) url.searchParams.set("cron", meta.cron);
  if (meta.force) url.searchParams.set("force", "1");

  const startedAt = Date.now();
  const response = await fetch(url.toString(), {
    headers: {
      "authorization": `Bearer ${env.EZ_TRIGGER_SECRET}`,
      "user-agent": "ez-trading-cloudflare-cron/1.0",
    },
  });
  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text.slice(0, 500) };
  }
  return {
    ok: response.ok,
    status: response.status,
    elapsedMs: Date.now() - startedAt,
    triggeredAtICT: ictStamp(),
    body,
  };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(triggerForecast(env, { source: "cloudflare-cron", cron: event.cron }));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return json({
        ok: true,
        service: "ez-trading-forecast-cron",
        triggerUrlConfigured: Boolean(env.TRIGGER_URL),
        secretConfigured: Boolean(env.EZ_TRIGGER_SECRET),
        updatedAtICT: ictStamp(),
      });
    }

    const expected = env.EZ_TRIGGER_SECRET;
    const auth = request.headers.get("authorization") || "";
    if (!expected || auth !== `Bearer ${expected}`) {
      return json({ ok: false, reason: "UNAUTHORIZED", updatedAtICT: ictStamp() }, 401);
    }

    const force = url.searchParams.get("force") === "1";
    const result = await triggerForecast(env, { source: "cloudflare-manual", force });
    return json(result, result.ok ? 200 : 502);
  },
};
