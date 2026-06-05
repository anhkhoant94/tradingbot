# Ez Trading Forecast Cron

Cloudflare Worker timer for Ez Trading dashboard forecast refresh.

The primary timer is a Durable Object Alarm that reschedules itself every 15 minutes during the Vietnam trading window. A Cloudflare Cron Trigger is kept as a best-effort backup:

```text
*/15 2-8 * * 1-5
```

Cloudflare cron is UTC, so this maps to roughly 09:00-15:45 ICT on weekdays. The Vercel trigger endpoint also has its own Vietnam-market-window guard.

## Deploy

From this folder:

```powershell
npx.cmd wrangler login
npx.cmd wrangler secret put EZ_TRIGGER_SECRET
npx.cmd wrangler deploy
npx.cmd wrangler triggers deploy
```

When prompted for `EZ_TRIGGER_SECRET`, paste the same value as `cron_secret` in:

```text
%USERPROFILE%\.cache\stock_screening_deploy_secrets.json
```

## Verify

Start the Durable Object timer:

```powershell
$secret = (Get-Content "$HOME\.cache\stock_screening_deploy_secrets.json" | ConvertFrom-Json).cron_secret
$headers = @{ Authorization = "Bearer $secret" }
Invoke-RestMethod -Headers $headers "https://ez-trading-forecast-cron.anhkhoant94.workers.dev/timer/start"
```

Health/state check:

```powershell
Invoke-RestMethod "https://ez-trading-forecast-cron.anhkhoant94.workers.dev/health"
Invoke-RestMethod -Headers $headers "https://ez-trading-forecast-cron.anhkhoant94.workers.dev/timer/state"
```

Manual trigger after deploy:

```powershell
$secret = (Get-Content "$HOME\.cache\stock_screening_deploy_secrets.json" | ConvertFrom-Json).cron_secret
$headers = @{ Authorization = "Bearer $secret" }
Invoke-RestMethod -Headers $headers "https://ez-trading-forecast-cron.<your-workers-subdomain>.workers.dev/?force=1"
```

Expected response from the downstream Vercel trigger is one of:

- `dispatched`
- `skip_running`
- `skip_recent_success`

The worker never stores GitHub credentials. It only calls the existing Vercel endpoint protected by `EZ_TRIGGER_SECRET`.

If Cloudflare Cron does not fire, the Durable Object Alarm still drives the timer. If the Durable Object state shows `enabled=false` or `pendingAlarm=null`, run `/timer/start` again.
