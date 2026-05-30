# E2E TEST PLAN — FULL-AUTO DASHBOARD 2026-05-30

## Pre-test trạng thái (đã verify trong Linux sandbox)

YAML lint cả 4 workflow:

```
dashboard-auto-refresh.yml: on_keys=['schedule', 'push', 'workflow_dispatch'], schedule=[{'cron': '2-59/5 * * * *'}]
screening-weekly.yml:       on_keys=['schedule', 'workflow_dispatch'], schedule=[{'cron': '0 23 * * 0'}]
bctc-monthly.yml:           on_keys=['schedule', 'workflow_dispatch'], schedule=[{'cron': '0 23 28-31 * *'}]
weekly-signal.yml:          on_keys=['schedule', 'workflow_dispatch'], schedule=[{'cron': '30 2 * * 0'}]
```

Health check public dashboard lúc 11:00 ICT:

```
{"data_as_of": "2026-05-21",
 "live_updated_at": "2026-05-30 10:11:28",
 "live_latest_price_date": "2026-05-29",
 "live_is_today": true,
 "vni_history_points": 6810}
```

Verdict baseline: cron 5p chạy OK (live update fresh), nhưng `data.js as_of` đang stale 9 ngày. Sau khi PUSH_WORKFLOW_FIX.bat đẩy `output/screening_summary.json` + `screening_full_results.csv` mới (2026-05-29), 1 lần cron tiếp theo sẽ regen `data.js` với as_of mới.

## Phase 1 — Push & verify dashboard-auto-refresh (5-10 phút)

1. Double-click `PUSH_WORKFLOW_FIX.bat` (root project)
2. Sau ~30 giây, script in `pushed <path>` cho từng file, kết thúc bằng `=== PUSH OK. ===`
3. GitHub Actions auto-trigger `dashboard-auto-refresh.yml` (vì path `output/**` thay đổi)
4. Theo dõi tại https://github.com/anhkhoant94/tradingbot/actions — run xanh trong 3-5 phút
5. Verify Vercel update:

```powershell
python tools/check_dashboard_public_health.py --require-fresh-live --require-vni-history
```

Pass criteria:
- `data_as_of` đổi từ `2026-05-21` → `2026-05-29`
- `live_is_today=true`
- `vni_history_points` không giảm

## Phase 2 — Manual dispatch screening-weekly (~15-20 phút lần đầu)

1. Vào https://github.com/anhkhoant94/tradingbot/actions/workflows/screening-weekly.yml
2. Bấm `Run workflow` → branch `main` → mode `opportunity` → Run
3. Theo dõi log: pip install + vnstock (~3 phút), restore cache, refresh price (~2 phút), run_stock_screen.py (~10-15 phút lần đầu vì cache trống, ~5 phút lần sau)
4. Step `Commit refreshed outputs` push `output/screening_summary.json` mới (as_of là ngày chạy)
5. Step `Trigger dashboard auto refresh` gọi workflow_dispatch sang dashboard-auto-refresh.yml
6. Trong 5 phút, Vercel sẽ deploy bản mới với as_of mới

Pass criteria:
- `output/screening_summary.json` commit mới trên main
- `data_as_of` trên Vercel = ngày chạy
- Không hỏi anh nhập gì

## Phase 3 — Manual dispatch weekly-signal (~5 phút)

1. Vào workflow `Weekly Signal Monday` → Run workflow → NAV `1000000000`
2. Run sinh `output/live_signals/<as_of>/briefing.md` + `target_portfolio.csv` + `orders.csv`
3. Commit auto vào repo
4. Mở `https://github.com/anhkhoant94/tradingbot/tree/main/output/live_signals` xem briefing tuần

Pass criteria:
- Có folder `output/live_signals/2026-05-29/` (hoặc ngày chạy)
- File `briefing.md` mở được, chứa overlay decision (BULL/CASH) + danh sách lệnh nếu có

## Phase 4 — Manual dispatch bctc-monthly với sample (~5 phút)

1. Vào workflow `BCTC Scrape Monthly` → Run workflow → sample_size `10`
2. Script scrape 10 mã đầu trong universe, update `.cache/backtest/cp68/*.parquet`
3. Commit `.cache/backtest/cp68` ngược về repo

Pass criteria:
- 10 file parquet có `Last-Modified` mới
- Commit `chore(bctc): monthly refresh 2026-05` được push

(Đối với run thực Khoa không cần làm gì — cron đêm cuối tháng tự chạy full universe.)

## Phase 5 — Verify cron tự động (sau 5-10 phút)

Sau Phase 1, cron `2-59/5 * * * *` sẽ chạy 1 lần trong vòng 5 phút. Verify lại:

```powershell
python tools/check_dashboard_public_health.py
```

Nếu `live_updated_at` đổi (giờ phút mới) thì cron đang chạy đúng schedule.

## Rollback (nếu cần)

Xóa workflow YAML trên GitHub (Settings → Actions → Workflow → Disable) hoặc push commit xóa file:

```powershell
del .github\workflows\screening-weekly.yml
del .github\workflows\bctc-monthly.yml
del .github\workflows\weekly-signal.yml
git add .github/workflows
git commit -m "rollback: disable full-auto workflows"
git push origin main
```

## Kết luận test

Nếu Phase 1-3 pass: dashboard 100% full-auto, anh không cần bật máy ngoài việc đặt lệnh thủ công tại broker.

Nếu Phase 2 fail vì `vnstock` package break: anh fallback bằng cách chạy `python run_stock_screen.py` local rồi git push manual như cũ.

Nếu Phase 4 fail vì cophieu68 ban IP: trong `bctc-monthly.yml` tăng `delay_s` từ 0.5 lên 1.5, hoặc thay scraper sang vnstock KBS.
