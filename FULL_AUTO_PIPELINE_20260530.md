# FULL-AUTO DASHBOARD PIPELINE — 2026-05-30

**Mục tiêu:** Dashboard hoạt động 24/7 không cần máy local. Mọi update (giá, BCTC, signal, redeploy) chạy trên GitHub Actions + Vercel.

## 1. Topology mới (4 workflow + 1 deploy)

```
[VPS histdatafeed]   [cophieu68.vn]   [vnstock KBS]
        \                |                /
         \               |               /
          \              |              /
   .github/workflows/screening-weekly.yml      [Sunday 23:00 UTC = Monday 06:00 ICT]
   .github/workflows/bctc-monthly.yml          [Day 1 monthly 23:00 UTC]
   .github/workflows/weekly-signal.yml         [Sunday 02:30 UTC = Sunday 09:30 ICT]
   .github/workflows/dashboard-auto-refresh.yml [Every 5 min]
                       |
                       v
            [GitHub repo anhkhoant94/tradingbot]
                       |
                       v
            [Vercel project trading-execution-desk-khoa]
                       |
                       v
            https://trading-execution-desk-khoa.vercel.app
```

## 2. Workflow files đã tạo

### 2.1 `.github/workflows/dashboard-auto-refresh.yml` (đã có, đã fix)
- Trigger: cron `2-59/5 * * * *` + push + manual
- Fix vừa thêm: `PYTHONPATH: ${{ github.workspace }}` → giải quyết import error `backtest`
- Trách nhiệm: refresh giá live, regen dashboard JS, deploy Vercel

### 2.2 `.github/workflows/screening-weekly.yml` (mới)
- Trigger: cron `0 23 * * 0` (Chủ Nhật 23:00 UTC = Thứ Hai 06:00 ICT)
- Steps: install deps + vnstock, restore cache, run `update_dashboard_live_data.py`, run `run_stock_screen.py`, regen JS bundles, commit outputs ngược về repo, dispatch dashboard-auto-refresh
- Output commits: `output/screening_full_results.csv`, `output/screening_summary.json`, `dashboard/data.js`, `dashboard/analysis.js`, `dashboard/history.js`
- Mode mặc định: `opportunity` (filter 1500 tỷ vốn hóa, 5 tỷ liquidity, 5k price). Manual dispatch có thể chọn `conservative`.

### 2.3 `.github/workflows/bctc-monthly.yml` (mới)
- Trigger: cron `0 23 28-31 * *` + gate UTC ngày mai = 01 (tức là chạy đêm cuối tháng UTC)
- Steps: scrape full universe BCTC qua `backtest/cp68_scraper.py`, commit `.cache/backtest/cp68/*.parquet`
- Lý do tần suất: BCTC quý ra sau Q-end 30-90 ngày, mỗi tháng kiểm 1 lần là đủ
- Free, không API key

### 2.4 `.github/workflows/weekly-signal.yml` (mới)
- Trigger: cron `30 2 * * 0` (Chủ Nhật 02:30 UTC = Chủ Nhật 09:30 ICT)
- Steps: refresh giá → chạy `backtest/live_signal_generator.py --nav 1_000_000_000` → commit `output/live_signals/<date>/` → dispatch dashboard-auto-refresh
- Output: `briefing.md`, `target_portfolio.csv`, `orders.csv`, `signal_state.json` cho tuần sắp tới

## 3. Bootstrap (lần chạy đầu tiên)

### 3.1 Push các file cấu hình + seed data

Anh chỉ cần double-click `PUSH_WORKFLOW_FIX.bat` ở root project. Script gọi `tools/deploy_online_dashboard_from_tokens.py --push`, đẩy nguyên FILES_TO_PUSH list lên repo qua GitHub Contents API. Token đọc từ `~/.cache/stock_screening_deploy_secrets.json`.

Danh sách bị push gồm:
- 4 workflow yml mới + cũ
- `requirements.txt` (đã thêm `vnstock>=3.0.0`)
- `dashboard/*` đã refresh ngày 29-05
- `output/screening_summary.json` + `output/screening_full_results.csv` (as_of 2026-05-29)
- 2 file md mới (audit + pipeline)

### 3.2 Cache lần đầu

GitHub Actions cache trống ở run đầu tiên. Workflow sẽ phải:
- Re-fetch toàn bộ price history từ VPS (10-15 phút cho 703 mã)
- Re-scrape BCTC nếu mã chưa có (45-60 phút cho 700 mã)

Cách giảm thời gian: anh chạy `tools/deploy_online_dashboard_from_tokens.py --push` rồi push thêm thư mục cache lớn:
```powershell
cd "C:\Users\User\Documents\Onedrive\Documents\New project 2\stock_screening"
git add .cache/backtest/cp68 .cache/backtest/vnindex_daily.parquet
git commit -m "seed: bootstrap BCTC + VNI cache"
git push origin main
```
Tổng size cp68 = 15 MB, VNI = 40 KB → ổn cho repo. Nếu anh không muốn nặng repo thì bỏ qua, workflow chỉ chạy chậm 1 lần đầu rồi cache GH Actions auto-restore từ run 2 trở đi.

## 4. Cost / quota estimate

| Loại | Free quota | Ước tính dùng | Verdict |
|---|---|---|---|
| GitHub Actions (public repo) | Không giới hạn | dashboard-auto-refresh 5p × 25p × 288 = ~7,200 phút/ngày, screening 90p/tuần, bctc 180p/tháng, signal 30p/tuần | Free vì public repo |
| GitHub Actions (private repo) | 2,000 phút/tháng | ~215,000 phút/tháng → vượt ngưỡng rất xa | Nếu chuyển private cần giảm cron xuống 15-30p |
| Vercel hobby | 100 GB-hr serverless | Dashboard tĩnh ~0 GB-hr | Free |
| VPS histdatafeed | Free, no key | ~3,000 request/ngày | OK |
| cophieu68.vn scrape | Free, no key, 0.5s delay | ~700 request/tháng | OK |
| vnstock KBS | Free, KBS public API | ~700 request/tuần | OK |

## 5. Quy trình chạm vào trong tuần điển hình (Khoa không cần mở máy)

- **Chủ Nhật 09:30 ICT** — `weekly-signal.yml` chạy, commit `output/live_signals/2026-MM-DD/`
- **Chủ Nhật 18:00 ICT** — anh có thể đọc briefing.md trên GitHub
- **Thứ Hai 06:00 ICT** — `screening-weekly.yml` chạy, refresh `screening_summary` + `data.js`
- **Thứ Hai 06:30 ICT** — `dashboard-auto-refresh.yml` chạy (cron 5p), deploy lên Vercel
- **Trong ngày** — cron 5p tiếp tục refresh giá realtime
- **Ngày 1 hàng tháng 06:00 ICT** — `bctc-monthly.yml` scrape BCTC mới

## 6. E2E test plan

Sau khi push lên main, anh hoặc Codex bấm Manual workflow_dispatch:

1. `dashboard-auto-refresh.yml` → verify `https://trading-execution-desk-khoa.vercel.app/data.js` đổi sang `as_of=2026-05-29`
2. `screening-weekly.yml` → verify `output/screening_summary.json` được commit với `as_of=2026-05-30` (hoặc ngày trigger)
3. `weekly-signal.yml` → verify thư mục `output/live_signals/<date>/briefing.md` mới được commit
4. `bctc-monthly.yml` → manual dispatch với `sample_size=5` để test nhanh, verify 5 file .parquet được update trong `.cache/backtest/cp68/`

Script verify nhanh sau push:
```powershell
python tools/check_dashboard_public_health.py --require-fresh-live --require-vni-history
```

Kỳ vọng:
- `live_is_today=true`
- `live_latest_price_date=2026-05-29` (Friday)
- `data.js summary.as_of` không còn `2026-05-21`

## 7. Verdict full-auto

Sau khi 4 workflow trên active:
- Live price refresh: AUTO (cron 5p)
- Screening + data.js refresh: AUTO (Chủ Nhật weekly)
- BCTC quarterly: AUTO (monthly)
- Weekly signal Monday orders: AUTO (Chủ Nhật)
- Dashboard deploy Vercel: AUTO (sau mỗi commit + cron 5p)

Còn lại 0 thao tác manual. Anh chỉ cần mở `https://trading-execution-desk-khoa.vercel.app` xem signal sáng thứ Hai và đặt lệnh thủ công (vì broker SSI/VPS không cho phép API trade tự động cho cá nhân — đây là rào cản pháp lý, không phải kỹ thuật).

## 8. Edge cases + rollback

- Nếu `vnstock` package break (đã có history vnstock 3.x update): pin version cứng trong requirements.txt khi ổn định. Hiện đặt `vnstock>=3.0.0`.
- Nếu cophieu68.vn ban IP: chuyển sang vnstock KBS làm primary, cp68 backup.
- Nếu screening-weekly fail (vnstock API down): dashboard-auto-refresh tiếp tục chạy với screening_summary cũ → vẫn refresh giá + P&L, chỉ không refresh top picks.
- Nếu Vercel down: Cloudflare tunnel cũ vẫn ở `tools/cloudflared.exe`, có thể kích hoạt lại như fallback.
- Rollback workflow: xóa file `.github/workflows/<name>.yml` rồi push, GH Actions tự ngưng.
