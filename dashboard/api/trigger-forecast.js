const WORKFLOW_FILE = "dashboard-auto-refresh.yml";
const MIN_RECENT_SECONDS = 12 * 60;

function ictParts(date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Ho_Chi_Minh",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date).reduce((out, p) => {
    out[p.type] = p.value;
    return out;
  }, {});
}

function ictStamp(date = new Date()) {
  const p = ictParts(date);
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second}`;
}

function withinMarketWindow(date = new Date()) {
  const p = ictParts(date);
  const weekday = ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(p.weekday);
  const minutes = Number(p.hour) * 60 + Number(p.minute);
  return weekday && minutes >= 8 * 60 + 45 && minutes <= 15 * 60 + 45;
}

async function githubJson(path, options = {}) {
  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) throw new Error("MISSING_GITHUB_DISPATCH_TOKEN");
  const response = await fetch(`https://api.github.com${path}`, {
    ...options,
    headers: {
      "Authorization": `Bearer ${token}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "ez-trading-vercel-cron",
      ...(options.headers || {}),
    },
  });
  if (response.status === 204) return null;
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const msg = payload && payload.message ? payload.message : text || `HTTP_${response.status}`;
    throw new Error(msg);
  }
  return payload;
}

async function recentWorkflowState(repo, branch) {
  const path = `/repos/${repo}/actions/workflows/${encodeURIComponent(WORKFLOW_FILE)}/runs?branch=${encodeURIComponent(branch)}&per_page=10`;
  const payload = await githubJson(path);
  const now = Date.now();
  for (const run of payload.workflow_runs || []) {
    const status = String(run.status || "");
    const ageSeconds = Math.max(0, (now - Date.parse(run.created_at)) / 1000);
    if (["queued", "in_progress", "waiting", "requested"].includes(status)) {
      return { action: "skip_running", runId: run.id, status, ageSeconds: Math.round(ageSeconds), url: run.html_url };
    }
    if (run.conclusion === "success" && ageSeconds < MIN_RECENT_SECONDS) {
      return { action: "skip_recent_success", runId: run.id, status, conclusion: run.conclusion, ageSeconds: Math.round(ageSeconds), url: run.html_url };
    }
  }
  return { action: "dispatch" };
}

module.exports = async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Content-Type", "application/json; charset=utf-8");

  const expected = process.env.CRON_SECRET;
  const auth = req.headers.authorization || "";
  if (expected && auth !== `Bearer ${expected}`) {
    res.status(401).json({ ok: false, reason: "UNAUTHORIZED", updatedAtICT: ictStamp() });
    return;
  }

  const force = String(req.query.force || "") === "1";
  const now = new Date();
  if (!force && !withinMarketWindow(now)) {
    res.status(200).json({ ok: true, action: "skip_outside_market_window", updatedAtICT: ictStamp(now) });
    return;
  }

  const repo = process.env.GITHUB_DISPATCH_REPO;
  const branch = process.env.GITHUB_DISPATCH_BRANCH || "main";
  if (!repo) {
    res.status(500).json({ ok: false, reason: "MISSING_GITHUB_DISPATCH_REPO", updatedAtICT: ictStamp(now) });
    return;
  }

  try {
    const state = await recentWorkflowState(repo, branch);
    if (state.action !== "dispatch") {
      res.status(200).json({ ok: true, ...state, updatedAtICT: ictStamp(now) });
      return;
    }
    await githubJson(`/repos/${repo}/actions/workflows/${encodeURIComponent(WORKFLOW_FILE)}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: branch }),
      headers: { "Content-Type": "application/json" },
    });
    res.status(200).json({ ok: true, action: "dispatched", workflow: WORKFLOW_FILE, repo, branch, updatedAtICT: ictStamp(now) });
  } catch (err) {
    res.status(500).json({ ok: false, reason: String(err && err.message || err), updatedAtICT: ictStamp(now) });
  }
};
