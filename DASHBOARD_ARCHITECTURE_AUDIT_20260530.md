# DASHBOARD ARCHITECTURE AUDIT — 2026-05-30

**Mục đích:** Map toàn bộ chuỗi data → engine → dashboard, identify component nào đã auto-cloud vs vẫn cần máy local. Đầu vào cho `FULL_AUTO_PIPELINE_20260530.md`.

## 1. Trạng thái hiện tại (snapshot 2026-05-30 11:00 ICT)

Public dashboard: `https://trading-execution-desk-khoa.vercel.app/`

| Endpoint | Giá trị hiện tại | Trạng thái |
|---|---|---|
| `/dashboard_live_update_status.json` | `updatedAt = 2026-05-30 10:11:28`, `latestPriceDate = 2026-05-29` | OK — cron 5 phút đang chạy |
| `/data.js` (`summary.as_of`) | `2026-05-21` | **STALE 9 ngày** — không update theo cron |
| `dashboard/data.js` local | `as_of = 2026-05-29` | Local fresh, chưa được push lên repo |

Kết luận sơ bộ: workflow `dashboard-auto-refresh.yml` chạy thành công, refresh được giá realtime và copy-trade P&L, nhưng KHÔNG refresh được screening result vì input của nó (`output/screening_summary.json`, `output/screening_full_results.csv`) là file repo, không được regenerate trong workflow.

## 2. Architecture diagram (text)

```
[A. PRICE LAYER — đã cloud]
   vps.com.vn histdatafeed (free API)
   └─ update_dashboard_live_data.py  ──> .cache/backtest/history/, history_clean/, vnindex_daily.parquet
                                       ──> dashboard/dashboard_live_update_status.json

[B. BCTC LAYER — local-only]
   cophieu68.vn/quote/financial_detail (free scrape, 0.4s/sym)
   └─ backtest/cp68_scraper.py + update_bctc() trong update_dashboard_live_data.py
        ──> .cache/backtest/cp68/<SYM>.parquet   [694 files, ~14 MB total]
   ! workflow chỉ scrape 8 symbol đầu trong holdings (max_workers=4) — full universe (703) chưa có

[C. SCREENING LAYER — local-only, KHÔNG cloud]
   run_stock_screen.py
   ├─ deps: vnstock package (Listing, Trading, Quote, Finance, KBS source)
   ├─ inputs: .cache/backtest/cp68/* + .cache/backtest/history_clean/* + .cache/universe.parquet
   ├─ runtime: ~10-15 phút full 703 mã
   └─ outputs: output/screening_full_results.csv, output/screening_summary.json
        (chứa universe filter, hard gates, composite score 0-100, sector/industry)

[D. SIGNAL/ENGINE LAYER — local-only]
   backtest/live_signal_generator.py (rank_best_full + VNI 8w overlay)
   ├─ deps: scores_2016_v4 dir (124 monthly files) + history_clean + vnindex_daily
   └─ outputs: output/live_signals/<date>/{briefing.md, target_portfolio.csv, orders.csv}

   backtest/beat_vni30_*.py (R46, flexible Monday entry, daily-lot sim)
   └─ outputs: output/beat_vni30_parallel/*/equity_curve_honest.parquet + trades.parquet

[E. DASHBOARD ASSEMBLY LAYER — đã cloud]
   generate_dashboard_data.py   ──> dashboard/data.js          (đọc output/screening_*.json + holdings)
   generate_deep_analysis.py     ──> dashboard/analysis.js     (đọc trades/holdings policies)
   generate_model_history.py     ──> dashboard/history.js      (đọc equity_curve_honest)
   tools/deploy_vercel_dashboard.py ──> Vercel REST API upload
        └─ alias https://trading-execution-desk-khoa.vercel.app

[F. CI/CD — đã cloud (partial)]
   .github/workflows/dashboard-auto-refresh.yml
   ├─ schedule: */5 minute
   ├─ steps: update_dashboard_live_data → generate_deep_analysis → generate_model_history → generate_dashboard_data → deploy Vercel → verify
   └─ env fix vừa thêm: PYTHONPATH=${{ github.workspace }}

[G. TUNNEL/LOCAL — đang dùng dự phòng]
   cloudflared (tools/cloudflared.exe) — chỉ chạy khi máy bật. Không cần khi Vercel + GH Actions full-auto.
```

## 3. Gap analysis — components CHƯA AUTO

| # | Component | Hiện tại | Vấn đề | Tác động |
|---|---|---|---|---|
| G1 | `run_stock_screen.py` | Local chạy bằng tay, output push qua git lên repo khi nhớ | Workflow đọc file repo stale → `data.js` không update | Bảng cổ phiếu trên dashboard luôn 1 tuần cũ |
| G2 | BCTC full universe (703 mã) | `update_bctc()` chỉ scrape 8 mã trong holdings | Khi mã mới vào watchlist, BCTC chưa được scrape | Score/valuation cho mã mới thiếu input |
| G3 | Score monthly file (`scores_2016_v4/*.parquet`) | Generate bởi `run_stock_screen.py` chạy local | Workflow không tự sinh file score mới | `live_signal_generator.py` (chưa được nối vào workflow) sẽ stale |
| G4 | `live_signal_generator.py` weekly run | Không trong workflow, không có cron riêng | Tuần mới không có signal Monday tự động | Vẫn phải tự gọi local trước khi đặt lệnh thứ Hai |
| G5 | `engine_repro_guard.py` audit weekly | Không cron | Không tự bắt regression khi codepath thay đổi | OK chấp nhận manual quarterly |
| G6 | Local Windows files vào repo | Phải `git commit && git push` thủ công | Khi anh không bật máy, repo không nhận update | Workflow chạy với input cũ |
| G7 | Vercel alias propagation | Có verify step nhưng đôi khi 12 retries vẫn fail | Hiếm, không phải gốc của stale data | Edge case |

## 4. Components ĐÃ AUTO (cloud-only, không cần máy local)

- Live price refresh từ VPS (5 phút/lần qua GH Actions)
- Holdings P&L recompute (giá vốn từ trades.parquet, giá TT từ live cache)
- Dashboard re-deploy lên Vercel sau mỗi workflow run
- VN-Index history merge vào dashboard `history.js`
- Vercel alias `trading-execution-desk-khoa.vercel.app` ổn định 24/7

## 5. Cost & quota hiện tại

- GitHub Actions: public repo trên `anhkhoant94/tradingbot` → free tier không giới hạn phút chạy. Cron 5 phút × 25 phút timeout × 288 lần/ngày = đáng kể, nhưng không tính phí. Nếu chuyển sang repo private, cap 2,000 phút/tháng → cần giảm tần suất.
- Vercel: hobby tier, không tính phí cho dashboard tĩnh.
- VPS histdatafeed: free, không key, không rate limit công khai.
- cophieu68.vn: free scrape HTML, ~0.4s/sym throttle để tránh ban IP.

## 6. Nguồn BCTC khả thi cho auto-scrape

| Nguồn | Free? | Realtime? | Implementation |
|---|---|---|---|
| cophieu68.vn (`backtest/cp68_scraper.py`) | YES | HTML scrape, 2-3 phút trễ sau khi cập nhật | Đã có code. Cron monthly 6am ngày 1 mỗi tháng đủ vì BCTC quý ra sau 45-90 ngày |
| VietStock finance API | YES nhưng cần phiên đăng nhập | Realtime | HTML, không trivial parse, không khuyến nghị |
| VNDirect/SSI public API | YES, limit | Realtime | Khả thi nhưng cần register API key |
| FiinPro | NO (paid ~22 triệu/năm) | Realtime, có forecast | Chỉ recommend nếu anh sẵn sàng chi |
| vnstock package (KBS source) | YES | Trễ ~1 ngày | Đã dùng trong run_stock_screen.py |

Khuyến nghị: giữ cp68 + vnstock KBS dual-source (đã làm). Không cần FiinPro paid.

## 7. Verdict architecture

Dashboard hiện 70% auto-cloud, 30% còn phụ thuộc máy local:
- Auto OK: live price, holdings P&L, Vercel deploy, cron 5 phút.
- Phụ thuộc local: screening pipeline (`run_stock_screen.py`), BCTC full universe, score generation, weekly signal.

Để đạt 100% full-auto, cần:
1. Thêm step `run_stock_screen.py` vào workflow (hoặc workflow tách riêng monthly cron).
2. Thêm step scrape BCTC full universe mỗi tháng (sau Q-end 45 ngày).
3. Thêm workflow `weekly_signal.yml` chạy sáng thứ Hai 06:00 ICT để sinh `output/live_signals/<date>/`.
4. Cache `.cache/backtest/cp68/` và `.cache/backtest/history_clean/` vào GH Actions cache hoặc commit vào repo (14MB cp68 OK commit, history nặng hơn nên dùng cache).

Chi tiết implement xem `FULL_AUTO_PIPELINE_20260530.md`.
