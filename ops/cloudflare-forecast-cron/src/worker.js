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

function inVietnamTradingWindow(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date).reduce((out, part) => {
    out[part.type] = part.value;
    return out;
  }, {});
  const weekday = ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(parts.weekday);
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  return weekday && minutes >= 8 * 60 + 45 && minutes <= 15 * 60 + 45;
}

function nextQuarterHour(date = new Date()) {
  const next = new Date(date.getTime());
  const minutes = next.getUTCMinutes();
  const add = 15 - (minutes % 15 || 15);
  next.setUTCMinutes(minutes + add, 5, 0);
  return next;
}

function nextVietnamMarketAlarm(date = new Date()) {
  let next = nextQuarterHour(new Date(date.getTime() + 30_000));
  for (let i = 0; i < 7 * 24 * 4; i += 1) {
    if (inVietnamTradingWindow(next)) return next;
    next = new Date(next.getTime() + 15 * 60_000);
  }
  return new Date(date.getTime() + 15 * 60_000);
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

export class ForecastTimer {
  constructor(ctx, env) {
    this.ctx = ctx;
    this.env = env;
  }

  async state() {
    const data = await this.ctx.storage.get([
      "enabled",
      "lastAlarmAtICT",
      "lastTriggerResult",
      "nextAlarmAt",
      "startedAtICT",
    ]);
    const pendingAlarm = await this.ctx.storage.getAlarm();
    return {
      ok: true,
      enabled: data.get("enabled") === true,
      startedAtICT: data.get("startedAtICT") || null,
      lastAlarmAtICT: data.get("lastAlarmAtICT") || null,
      nextAlarmAt: data.get("nextAlarmAt") || (pendingAlarm ? new Date(pendingAlarm).toISOString() : null),
      pendingAlarm: pendingAlarm ? new Date(pendingAlarm).toISOString() : null,
      lastTriggerResult: data.get("lastTriggerResult") || null,
      updatedAtICT: ictStamp(),
    };
  }

  async scheduleNext(now = new Date()) {
    const next = nextVietnamMarketAlarm(now);
    await this.ctx.storage.put("nextAlarmAt", next.toISOString());
    await this.ctx.storage.setAlarm(next.getTime());
    return next;
  }

  async fetch(request) {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/state")) {
      return json(await this.state());
    }
    if (url.pathname.endsWith("/start")) {
      await this.ctx.storage.put("enabled", true);
      await this.ctx.storage.put("startedAtICT", ictStamp());
      const immediate = url.searchParams.get("immediate") === "1";
      const next = immediate ? new Date(Date.now() + 5_000) : await this.scheduleNext();
      if (immediate) {
        await this.ctx.storage.put("nextAlarmAt", next.toISOString());
        await this.ctx.storage.setAlarm(next.getTime());
      }
      return json({ ok: true, action: "started", nextAlarmAt: next.toISOString(), updatedAtICT: ictStamp() });
    }
    if (url.pathname.endsWith("/stop")) {
      await this.ctx.storage.put("enabled", false);
      await this.ctx.storage.deleteAlarm();
      await this.ctx.storage.put("nextAlarmAt", null);
      return json({ ok: true, action: "stopped", updatedAtICT: ictStamp() });
    }
    return json({ ok: false, reason: "NOT_FOUND", updatedAtICT: ictStamp() }, 404);
  }

  async alarm() {
    const enabled = await this.ctx.storage.get("enabled");
    const now = new Date();
    await this.ctx.storage.put("lastAlarmAtICT", ictStamp(now));
    let result = { ok: true, action: "skip_outside_market_window", updatedAtICT: ictStamp(now) };
    if (enabled === true && inVietnamTradingWindow(now)) {
      result = await triggerForecast(this.env, { source: "cloudflare-do-alarm" });
    }
    await this.ctx.storage.put("lastTriggerResult", result);
    if (enabled === true) {
      await this.scheduleNext(now);
    }
  }
}

function timerObject(env) {
  const id = env.FORECAST_TIMER.idFromName("ez-trading-forecast-timer");
  return env.FORECAST_TIMER.get(id);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(triggerForecast(env, { source: "cloudflare-cron", cron: event.cron }));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      const timerState = await timerObject(env).fetch("https://timer/state").then((r) => r.json()).catch(() => null);
      return json({
        ok: true,
        service: "ez-trading-forecast-cron",
        triggerUrlConfigured: Boolean(env.TRIGGER_URL),
        secretConfigured: Boolean(env.EZ_TRIGGER_SECRET),
        durableTimerConfigured: Boolean(env.FORECAST_TIMER),
        timerState,
        updatedAtICT: ictStamp(),
      });
    }

    const expected = env.EZ_TRIGGER_SECRET;
    const auth = request.headers.get("authorization") || "";
    if (!expected || auth !== `Bearer ${expected}`) {
      return json({ ok: false, reason: "UNAUTHORIZED", updatedAtICT: ictStamp() }, 401);
    }

    if (url.pathname === "/timer/start") {
      const immediate = url.searchParams.get("immediate") === "1" ? "?immediate=1" : "";
      return timerObject(env).fetch(`https://timer/start${immediate}`);
    }
    if (url.pathname === "/timer/stop") {
      return timerObject(env).fetch("https://timer/stop");
    }
    if (url.pathname === "/timer/state") {
      return timerObject(env).fetch("https://timer/state");
    }

    const force = url.searchParams.get("force") === "1";
    const result = await triggerForecast(env, { source: "cloudflare-manual", force });
    return json(result, result.ok ? 200 : 502);
  },
};
