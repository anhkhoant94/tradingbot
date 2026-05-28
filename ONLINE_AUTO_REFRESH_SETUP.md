# Online Auto Refresh Setup (No Manual Deploy)

This project now includes:

- `.github/workflows/dashboard-auto-refresh.yml`

What it does:

1. Every 10 minutes:
   - run `update_dashboard_live_data.py`
   - run `generate_deep_analysis.py`
   - run `generate_model_history.py`
   - deploy `dashboard/` to Vercel using API
2. Can also be run manually via **Actions -> Dashboard Auto Refresh -> Run workflow**.

## Required GitHub secrets/variables

Set in GitHub repo **Settings -> Secrets and variables -> Actions**:

- Secret:
  - `VERCEL_TOKEN`
- Variables:
  - `VERCEL_PROJECT` = `stock-screening-dashboard` (or your project name)
  - Optional: `VERCEL_TEAM_SLUG`
  - Optional: `VERCEL_TEAM_ID`

## Notes

- The online Vercel dashboard is static. The `Update` button in online mode is intentionally disabled.
- Data refresh is handled by this workflow and redeploy, not by browser button click.
- If runtime is close to schedule interval, workflow concurrency keeps only the latest run.
