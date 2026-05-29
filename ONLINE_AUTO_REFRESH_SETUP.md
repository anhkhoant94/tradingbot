# Online Auto Refresh Setup

## Latest Diagnosis 2026-05-29

- Public dashboard URL: `https://trading-execution-desk-khoa.vercel.app/`.
- GitHub Actions runs are reported as successful, but the public URL is still stale:
  - `/data.js` is still `as_of=2026-05-21`.
  - `/dashboard_live_update_status.json` is still `updatedAt=2026-05-28 18:38:03`.
- Local files are fresh:
  - `dashboard/data.js` is `as_of=2026-05-29`.
  - `dashboard/dashboard_live_update_status.json` is `updatedAt=2026-05-29 09:50:10`.
- Local live price source works: `update_dashboard_live_data.py` refreshed prices `10/10`, latest `2026-05-29`.

Verdict: this is not a data-source failure. It is a GitHub Actions / Vercel project / Vercel alias routing problem.

## Required GitHub Actions Settings

In GitHub repo `anhkhoant94/tradingbot`, go to:

`Settings -> Secrets and variables -> Actions`

Required secret:

- `VERCEL_TOKEN`

Required variables:

- `VERCEL_PROJECT=trading-execution-desk-khoa`
- `VERCEL_PUBLIC_URL=https://trading-execution-desk-khoa.vercel.app`

Optional, only if the Vercel project is under a team:

- `VERCEL_TEAM_ID`
- or `VERCEL_TEAM_SLUG`

## Workflow Requirements

The workflow `.github/workflows/dashboard-auto-refresh.yml` must:

1. Run `python update_dashboard_live_data.py`.
2. Run `python generate_deep_analysis.py`.
3. Run `python generate_model_history.py`.
4. Run `python generate_dashboard_data.py`.
5. Deploy `dashboard/` to Vercel.
6. Verify the public URL with a freshness check.

The local patched workflow uses:

- schedule: `2-59/5 * * * *`
- default project: `trading-execution-desk-khoa`
- freshness URL: `https://trading-execution-desk-khoa.vercel.app`

## Verification

After rerunning `Dashboard Auto Refresh`, run:

```powershell
python tools/check_dashboard_public_health.py --require-fresh-live
```

Expected:

- `live_is_today=true`
- `live_latest_price_date=2026-05-29` or current trading date
- `/data.js` no longer stuck at `2026-05-21`

If the workflow is green but this check still fails, inspect Vercel project/team alias routing and force redeploy the project that owns `trading-execution-desk-khoa.vercel.app`.
