# Ez Trading Dashboard Live Source Rules

This file is a mandatory project note for Codex, Claude, Mavis, and any other agent working in this repository.

## Canonical Public Link

Use only this public dashboard URL:

`https://ez-trading.vercel.app`

Do not use, deploy to, verify against, or share the old URL:

`https://trading-execution-desk-khoa.vercel.app`

The old Vercel alias was deleted by alias UID on 2026-06-10 and returned `404 DEPLOYMENT_NOT_FOUND` after cleanup. The old Vercel project `trading-execution-desk-khoa` was also deleted. If that old URL ever returns 200 again, treat it as a regression and remove it immediately.

## Deploy Target

The only active Vercel project is:

`ez-trading`

Expected GitHub Actions variables:

- `VERCEL_PROJECT=ez-trading`
- `VERCEL_PUBLIC_URL=https://ez-trading.vercel.app`

Before any deploy or public verification, explicitly confirm the target project/link is `ez-trading` / `https://ez-trading.vercel.app`.

## Online vs Local Data

Online Ez is the live reference.

The online dashboard is a static deployment, but its JSON artifacts are refreshed by cloud workflows:

- price/live status: roughly every 5 minutes
- R46 forecast: roughly every 15 minutes

Local files are only a dev snapshot unless explicitly synced/refetched.

Do not assume local files are current:

- `dashboard/*.json`
- `output/*.json`
- `output/dashboard_policies/r46_bear_stop_mcore/trades.parquet`
- `output/dashboard_policies/r46_bear_stop_mcore/equity_curve.parquet`
- `.cache/backtest/**`

When checking whether live price, forecast, paper trade, or dashboard state is current, fetch public Ez artifacts first:

- `https://ez-trading.vercel.app/dashboard_live_update_status.json`
- `https://ez-trading.vercel.app/full_universe_live_update_status.json`
- `https://ez-trading.vercel.app/r46_forecast.json`
- `https://ez-trading.vercel.app/r46_execution_state.json`
- `https://ez-trading.vercel.app/`

Local historical R46 model artifacts may legitimately stop at `2026-05-25`; online forecast/live artifacts may be current to the market date. This difference is expected unless local has just been synced from online or recomputed.

## 2026-06-10 Checkpoint

At the checkpoint below, online and local were intentionally different:

- Online `dashboard_live_update_status.json`: `updatedAtICT=2026-06-10 18:01:25`, `latestPriceDate=2026-06-10`.
- Online `full_universe_live_update_status.json`: `updatedAtICT=2026-06-10 18:04:31`, `latestPriceDate=2026-06-10`, `549/688` symbols at target, usable for forecast.
- Online `r46_forecast.json`: `status=COMPUTED`, `asOf=2026-06-10`, `computedAtICT=2026-06-10 18:05:48`, `planDate=2026-06-15`.
- Local `output/dashboard_policies/r46_bear_stop_mcore/trades.parquet`: stopped at `2026-05-25`.
- Local `output/dashboard_policies/r46_bear_stop_mcore/equity_curve.parquet`: stopped at `2026-05-25`.

## Operational Rule

If a user asks "is the dashboard live/current/updated/forecast computed?", answer from public Ez JSON first. Use local files only to inspect code or rebuild artifacts, not as the source of truth for live state.
