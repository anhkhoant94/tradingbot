# CLAUDE.md — Stock Screening Project Context

**CRITICAL DASHBOARD LIVE RULE 2026-06-10:** Before any dashboard deploy/check, read `DASHBOARD_LIVE_SOURCE_RULES.md`. Canonical public URL is only `https://ez-trading.vercel.app`. Do not use the old `trading-execution-desk-khoa` URL/project. Online Ez JSON artifacts are the live source of truth; local files are only a dev snapshot unless explicitly synced/refetched.

**ALWAYS READ NEXT:** `AI_SHARED_RESEARCH_LEDGER.md` in project root. It is the compact shared Codex/Claude decision ledger for latest pass/fail lanes, "do not rerun" notes, and current next actions. Update that file after every major run to avoid wasting usage. User runtime rule: smoke test first, expand only if useful; never burn long usage on a broad grid without early evidence. Collaboration mode: Codex and Claude are peer researchers; both may propose, run, and audit, with cross-review required before robust production/dashboard promotion.

**Cost convention:** in current strict daily/copy-trade engines, "15bps" means extra execution slippage per side. Base costs are already modeled separately: 15bps buy fee, 15bps sell fee, and 10bps sell-side personal income tax.

**CLAUDE NOTE 2026-06-10 (chiều) — PX independent lane CLOSED:** Hướng độc lập R46 (breakout R-1 + liquid momentum + style router, Phase R strict engine, data refetch) NEGATIVE: mọi cell ≤ +2% CAGR full; **perfect-router hindsight ceiling chỉ 26.35%, 4/11** — cả 2 sleeve mất alpha 2023-2026. R-1 38.21% (2016-2020) không reproduce trên history_2012 refetch. Đừng rerun breakout/momentum/router family này. Verdict: `output/px1_independent_20260610/VERDICT_PX_INDEPENDENT_LANE.md`.

**LATEST CLAUDE NOTE 2026-06-10:** RESEARCH HIT mới — H6P-capped stack trên engine R46 thật: `cliff_hv30` = R46 pinned + sideways vni13gt4_gross85 + liq5ty + H6P-capped vol-boost (hv=3.0, tv=0.90, breadth ramp 0.38-0.50 → 0.20-1.0, cap 0.55/symbol, gross ≤ 1.0). **CAGR 54.94%, MaxDD -25.76%, Sharpe 1.78, recent VNI+30 6/6, min edge 34.95pp, all-years 7/11, 0 T+2.5.** Pareto trội hơn R46 (46.75/-27.61) và sideways best (50.94/-28.67) mọi chiều. Cost 18/20bps PASS, plateau hv 1.75-3.5, cliff hv4.0, reproduce bit-exact. Caveat: remove-top1 (BSR) FAIL — concentration risk kế thừa sideways lane. Status: **RESEARCH_ONLY / PEER_REVIEW_PENDING** — chờ Codex audit + anh approve, KHÔNG ảnh hưởng R46 paper-trade hiện hành. Handoff: `output/beat_vni30_parallel/CLAUDE_H6P_STACK_HIT_HANDOFF_FOR_CODEX_20260610.md`.

**LATEST CODEX NOTE 2026-05-27 19:10 ICT:** New research hit in `output/beat_vni30_parallel/steady_trend_execution_extension_20260527/`. Strict daily 100-lot, flexible/baseline core + post-shock steady-trend overlay, stress20 result VNI+20 6/6, VNI+30 2/6, CAGR 61.04%, MaxDD -27.74%, min gap to +20 only +0.12pp. Stress15 min gap +2.10pp; stress25 falls to 5/6. Verdict: RESEARCH_ONLY / PEER_REVIEW_PENDING, not dashboard promotion. Read `AI_SHARED_RESEARCH_LEDGER.md` and `output/beat_vni30_parallel/overnight_collab/codex_to_claude/latest.md` before continuing.

**Cập nhật lần cuối:** 2026-05-27
**Owner:** Lưu Anh Khoa — Finance Director, YEG (anhkhoant94@gmail.com)
**Mục đích file này:** Cung cấp context đầy đủ cho Claude/Codex session mới, tránh context rot. Đọc 5 phút là vào việc được.

**HOT NOTE 2026-05-27 (FINAL Phase 1 optimized):** Sau anh challenge cap50, em sweep cap 40-100% + Phase 1 combo (V8 overlay + adaptive BB cap). **Best candidate: M_cap55_V8_adapt30bb** — CAGR **35.13%**, MDD **-26.95%**, Sharpe **1.39**, Calmar 1.30, **pv30 5/11** (2018/21/22/23/24). Improvement vs baseline cap50: +0.54pp CAGR, +3.81pp MDD, +0.10 Sharpe. Recipe: deadside guard + cap 55% top-1 default, cap 30% trong BROAD_BULL, V8 cash overlay (VNI 13w ret < -8% lag 1w). Verdict: `output/beat_vni30_parallel/PAIR657_CAP50_FINAL_OPTIMIZED_20260527.md`. Phase 2 (extend 2012-2015) BLOCKED — BCTC historical chỉ từ 2015-Q1 (vnstock Community 4 kỳ limit, không Sponsor). Options: tech-only matrix (~3-4h), scrape Vietstock (~6-10h), Sponsor sub, hoặc accept 11-năm evidence. Next steps cần làm: stress 30bps slippage + liq 5 tỷ floor + remove-symbol stress + threshold plateau ±20% trên M trước paper-trade.

**Codex parallel finding 2026-05-27 (regime diagnostic):**
- Output: `output/beat_vni30_parallel/PAIR657_PARALLEL_VERDICT_20260527.md`
- Regime attribution Pair657: BROAD_BULL +292.69pp (mix 2017/2020 fail với 2021 mania), NARROW_BULL +207.74pp, MICRO_LEADERSHIP +147.59pp, LIQUID_LEADERSHIP +165.72pp (cần liquidity fix confirm), BEAR -3.55pp, SIDEWAYS -32.02pp.
- Overlay smoke `cash_market_sideways_bear`: CAGR 44.5→47.4%, MaxDD -48.3→-26.2%, nhưng vẫn fail 2017 (broad bull large-cap led).
- Phương án router 3-mode đã chốt: SIDEWAYS/BEAR = cash, UP/MICRO leadership = Pair657, BROAD/RECOVERY = liquid-quality fallback (rank_best_full).
- Sửa hypothesis em ban đầu: Pair657 KHÔNG thắng BEAR (edge -3.55pp). Sweet spot là leadership UP, không phải bear-only.

## 1. User preferences quan trọng

Anh Khoa xưng "em" với Claude, gọi anh từ message đầu, **không cần được nhắc**. Trả lời tiếng Việt. Văn phong: văn xuôi mạch lạc, dẫn chứng số liệu, **không gạch đầu dòng trừ bảng dữ liệu**. Không dùng cụm AI sáo rỗng. Đơn vị: tỷ đồng khi văn nói, nguyên số khi báo cáo.

Khi anh nói "ngáo à, check lại" → DỪNG, đọc lại nguồn, không vội fix tiếp.

## 2. Project mission

Build engine pure-stock cho NAV cá nhân của anh. Target gốc: **mỗi năm 2021-2026 ≥ 30% return + beat VNI mỗi năm**. Target active mới từ 23/05/2026: **mỗi năm strategy return ≥ VNINDEX return + 30 điểm %**. Sau khi đạt gate 6/6 này, tiếp tục tối đa hóa **CAGR càng cao càng tốt**, không dừng ở cấu hình 6/6 đầu tiên. Strict T-1/T (signal tối T-1, khớp sáng T).

**Constraint hard (anh đã confirm 23/05/2026):**
- Pure stock only — KHÔNG ETF, KHÔNG bond fund, KHÔNG margin, KHÔNG short
- Cash overlay được phép (cash yield 0%, không gửi TGTK/bond fund)
- Strict T-1/T, no leakage, honest MTM
- Backtest period: 2016-2026 (10.3 năm)

## 3. Trạng thái hiện tại (sau hunt 23/05/2026)

**Pass30 tuyệt đối:** chưa có candidate đạt pass30 = 6/6 năm với constraint trên. Hunt exhaustive **34,425 configurations** trên các engine/overlay hiện hữu max đạt pass30 = 3/6 năm. Codex Lane A ngày 23/05 chạy thêm direct raw candidate search **4,801 cấu hình pure-stock**; max mới đạt **pass30 = 4/6**, vẫn hụt 2022 và 2026 nên **chưa được apply dashboard**.

**Target active mới — beat VNI +30pp mỗi năm:** sau Codex wave 2, best hiện tại đạt **5/6 năm** theo metric mới nhưng fail 2026 rất sâu. Target năm theo VNI hiện tại: 2021 cần +63.8%, 2022 cần -1.9%, 2023 cần +39.5%, 2024 cần +42.9%, 2025 cần +67.9%, 2026 YTD cần +37.7%. Bottleneck mới: **selector/regime gate cho 2026**, không còn là 2022.

**Best beat VNI +30pp candidate (research-only, chưa production):**
- Output: `output/beat_vni30_parallel/codex_aggregate/summary.md`
- Source: `output/beat_vni30_parallel/codex_lane_b2_cluster/`, family `sector_cluster`, run_id 599
- Beat VNI +30pp: **5/6**
- Yearly edge: 2021 +909.4pp, 2022 +30.4pp, 2023 +66.4pp, 2024 +61.7pp, 2025 +159.0pp, 2026 **-59.9pp**
- CAGR **113.4%**, MaxDD **-58.5%**
- Verdict: **NOT PRODUCTION** vì 2026 crash; cần selector/regime gate trước khi dashboard.

**Best honest config tìm được:**
- Engine: `rank_best_full` (5-6 mã, composite score ≥ 80, weekly rebal)
- Overlay: VNI 8w return < -6% → 100% cash, lag 2 tuần
- CAGR full 2016-2026: **19.88%**
- MaxDD: -34.15%
- Pass30: 3/6 năm 2021-2026 (chỉ 2021, 2023, 2025)
- Beat VNI: 5/6 năm 2021-2026
- OOS 2016-2021: CAGR 22.92%, pass30 2/6 (chỉ 2017, 2021)

**Best direct-search candidate mới (research-only, chưa production):**
- Output: `output/pass30_parallel/codex_lane_a_aggregate/summary.md`
- Source best: `output/pass30_parallel/codex_lane_a_y2026/best_stock_only/`
- Pure stock, cash yield 0%, no short/hedge, max gross ≤ 100%
- CAGR 2021-2026: **94.21%**, MaxDD **-31.10%**, beat VNI **6/6**, pass30 **4/6**
- Yearly: 2021 **+236.6%**, 2022 **+11.6%**, 2023 **+35.9%**, 2024 **+306.0%**, 2025 **+55.0%**, 2026 **+8.4%**
- Verdict: **NOT PRODUCTION** vì 2022 và 2026 dưới 30%, cần Claude audit no-leak/overfit; dashboard vẫn blocked.

**Lý do impossibility:** Năm 2022 VNI -34%, pure stock với mọi engine base -20 đến -38%. Cash overlay max đẩy về 0% (vì yield 0%), không thể +30%. Năm 2026 YTD (5 tháng đầu): -14 đến -23% base, không có catalyst nâng lên +30%.

**Codex G2 update 2026-05-23 (research hit, chưa production):**
- Target active beat VNI +30pp đã có candidate đạt **6/6** trong research backtest.
- Output: `output/beat_vni30_parallel/g2_mutation_breakout/best_stock_only/`
- Audit rerun: `output/beat_vni30_parallel/g2_candidate_audit/AUDIT.md`
- Handoff cho Claude: `output/beat_vni30_parallel/CODEX_G2_HIT_HANDOFF_FOR_CLAUDE.md`
- Candidate family: `breakout`, mutation quanh label-search breakout; dùng Claude F2 labels với no-future backward join và external H11.
- Metrics: Beat VNI +30pp **6/6**, pass30 absolute **6/6**, CAGR **107.06%**, MaxDD **-36.68%**, min edge vs VNI **+38.72pp**.
- Yearly: 2021 +274.9%, 2022 +40.5%, 2023 +115.1%, 2024 +59.2%, 2025 +93.7%, 2026 YTD +46.4%.
- Constraint smoke: pure stock PASS, cash yield 0% PASS, no ETF/bond/margin/short PASS, max gross <=100% PASS, rerun metrics match saved metrics.
- Critical caveats: **NOT DASHBOARD YET**. Min selected liquidity only **1.01 tỷ/ngày**, max single-stock weight **79.86%**, mutation overfit risk high. Needs Claude independent audit, liquidity/slippage stress, and VNI anchor reconciliation before copy-trade dashboard.

**Codex G2 pre-production update after Claude audit (2026-05-23):**
- Claude G2-E/F/G found structural flaws in run 599: no cash overlay, single-stock concentration, sector overheat blindness, cost sensitivity.
- Codex implemented `weekly_selector_labels.csv` into `backtest/pass30_direct_search.py`: `risk_floor_required`, `cluster_overheat`, `winner_protect_ok`, `rotation_reentry_ok`.
- New chosen candidate: `output/beat_vni30_parallel/g2_preproduction_candidate/`
- Source: `output/beat_vni30_parallel/g2_safe_mutation_cap33_liq3_cost15_from_cap33a/best_stock_only/`
- Handoff: `output/beat_vni30_parallel/CODEX_G2_PREPROD_HANDOFF_FOR_CLAUDE.md`
- Metrics: beat VNI +30pp **6/6**, pass30 absolute **5/6**, CAGR **71.00%**, MaxDD **-24.95%**, min edge **+30.37pp**, min buffer **+0.37pp**.
- Yearly: 2021 +192.2%, 2022 +8.0%, 2023 +39.9%, 2024 +50.9%, 2025 +77.6%, 2026 YTD +42.7%.
- Risk controls in candidate: max single-stock **33%**, min selected liquidity **3.065 tỷ/ngày**, extra slippage **0.15%/side**, cash yield 0%, no ETF/bond/margin/short.
- Stress caveat: fails at extra slippage **0.30%/side** and min liquidity **5 tỷ/ngày**. **Dashboard still blocked** until independent audit accepts thin +0.37pp buffer and live cost assumptions.

**Strict T-1/T audit update 2026-05-23 — CANDIDATE REJECTED PRODUCTION:**
- User asked to recheck strict T-1/T before dashboard because prior phases often failed after retest.
- Verdict file: `output/beat_vni30_parallel/G2_STRICT_AUDIT_VERDICT.md`
- Conservative one-week execution lag: pre-prod candidate drops from 6/6 to **0/6**, CAGR -0.53%, MaxDD -54.60%.
- Daily Monday execution proxy (signal from prior Friday weekly bar, execute next Monday close): **1/6**, CAGR 33.48%, MaxDD -34.89%; only 2021 passes, 2022 edge +29.0pp and 2026 edge +29.5pp both just miss.
- Strict-lag mutation search 601 runs under cap33/min-liq3/cost15 found best only **1/6**.
- Root issue: original weekly engine uses prior Friday close both as signal close and effective entry close, so it is not strict live-executable T-1/T. Dashboard copy-trade must remain BLOCKED. Next work must rebuild around daily execution timing from the start.

**Codex flexible-entry update 2026-05-23 — DASHBOARD CANDIDATE (conditional):**
- Anh clarified practical execution: after Friday signal, Monday open is tradable; if Monday gaps up hard vs Friday close, wait through the next 2 sessions for a pullback toward Friday close; if no pullback, skip. If Monday opens below Friday close, buying is allowed because execution is better than signal close.
- New engine: `backtest/beat_vni30_flexible_entry_search.py`, full-calendar daily-entry accounting. Important bug fixed inside this lane: early daily-entry version only wrote NAV rows when holdings existed, so cash/no-fill weeks disappeared from yearly returns.
- Best candidate: `output/beat_vni30_parallel/g2_flexible_entry_hit_mutation_buffer/best_stock_only/`
- Audit: `output/beat_vni30_parallel/g2_flexible_entry_buffer_candidate_audit/AUDIT.md`
- Handoff: `output/beat_vni30_parallel/CODEX_FLEXIBLE_VNI30_CANDIDATE_HANDOFF.md`
- Dashboard package: `output/dashboard_policies/flexible_vni30_candidate/`
- Metrics: beat VNI +30pp **6/6**, min edge **+33.36pp**, CAGR **72.71%**, MaxDD **-24.09%**, average exposure **60.11%**, max single-stock **33%**, min executed liquidity **3.065 tỷ/ngày**.
- Yearly: 2021 +187.6%, 2022 +9.7%, 2023 +42.9%, 2024 +46.9%, 2025 +89.2%, 2026 YTD +45.5%.
- Execution/risk: buy open if Monday gap <= 7%; if gap > 7%, wait 2 sessions for previous-close pullback; no fill => skip. Symbol stop -2.5% for 2 weeks only when breadth >= 0.25 and VNI 13w >= -2%.
- Caveat: **cost-sensitive**. Fails 6/6 at extra slippage 0.30%/side and at min liquidity 5 tỷ/ngày. Dashboard can show it only as a conditional candidate under explicit assumptions: 0.15%/side slippage, min liquidity 3 tỷ/ngày, pure stock, cap 33%.
- Dashboard updated and browser-verified at `http://127.0.0.1:8765/index.html`: default policy `VNI+30 Candidate`, copy-trade holdings GEE/VIC/PVP visible, investor-facing methodology panel visible, no console errors observed; mobile 390px has no horizontal overflow.

**Codex latency/T+2.5 update 2026-05-23 — dashboard refreshed:**
- Anh suggested reducing signal delay and allowing the pullback wait to extend as long as sale respects T+2.5.
- Updated `backtest/beat_vni30_flexible_entry_search.py`: added `entry_min_sell_sessions`, `daily_stop_loss`, and T+2.5-aware fill rejection.
- Added stricter daily cash/lot simulator `backtest/beat_vni30_daily_execution_sim.py`; smoke result on old rule was only 4/6, so this is the next production-hardening lane.
- Best T+2.5 strict flexible candidate: `output/beat_vni30_parallel/g2_latency_tplus3_mutation_v1/best_stock_only/`
- Update summary: `output/beat_vni30_parallel/LATENCY_REDUCTION_TPLUS_UPDATE.md`
- Metrics: beat VNI +30pp **6/6**, CAGR **75.95%**, MaxDD **-24.09%**, min edge **+30.34pp**. Rule: gap threshold 9%, min sell sessions 3, daily stop-loss 0%.
- Stress remains weak: slippage 0.30%/side => **4/6**, min-liq 5 tỷ => **3/6**.
- Dashboard package refreshed and browser verified: default `VNI+30 Candidate`, methodology mentions T+2.5, header Ready, holdings GEE/VIC/PVP visible, no console errors. Screenshot: `output/beat_vni30_parallel/dashboard_vni30_candidate_tplus_verified.png`.

**Dashboard copy-trade/P&L fix 2026-05-23:**
- Anh reported dashboard still had stale price/history behavior and copy trade did not show cost basis, market price, and current P/L.
- Fix doc: `output/beat_vni30_parallel/DASHBOARD_COPYTRADE_PNL_FIX.md`
- `generate_deep_analysis.py` now computes candidate current price from `.cache/backtest/history_clean/<symbol>.parquet`, exposes `priceAsOf`, `currentValueMil`, `costMil`, `currentPnlMil`, `currentPnlPct`.
- Latest dashboard refresh pulled GEE/VIC/PVP and VNINDEX to **2026-05-22** via VCI/KBS; entry price now uses live execution fill (Monday open/pullback rule), not weekly close snapshot.
- `dashboard/index.html` + `dashboard/app.js` now show copy-trade columns `Giá vốn`, `Giá TT`, `Lãi/lỗ`; holdings table shows market price date.
- Created non-empty `output/dashboard_policies/flexible_vni30_candidate/trades.parquet` from weekly rebalance ledger; `history.js` now shows **627 records** from 2021-01-04 to 2026-05-18 instead of empty ledger.
- Data-level verified after refresh: GEE entry 121.5k vs market 108.8k P/L -31.8tr/-10.45%; VIC 226.8k vs 216.5k P/L -12.4tr/-4.54%; PVP 20.15k vs 18.05k P/L -26.9tr/-10.42%. HTTP served `index.html`, `analysis.js`, and `history.js` return 200.
- Caveat: current `trades.parquet` is weekly rebalance ledger reconstructed from target holdings, with latest rows aligned to live-fill prices, not final daily cash/lot ledger. Next Codex lane should produce ledger from optimized daily simulator.

**Codex Lane L daily-lot audit 2026-05-23 — production blocked:**
- Exact current dashboard config tested with `backtest/beat_vni30_daily_execution_sim.py`.
- Output: `output/beat_vni30_parallel/codex_lane_l_daily_lot_current_config/`
- Verdict: beat VNI +30pp only **4/6**, CAGR 70.26%, MaxDD -30.50%, min edge +1.46pp.
- Pass: 2021, 2022, 2023, 2025. Fail: **2024** edge +21.8pp and **2026** edge +1.5pp.
- Dashboard wording updated to candidate preview / daily-lot production audit failed 4/6. Do NOT claim production-ready copy trade until daily-lot optimization recovers 6/6.
- Dashboard rebalance labels fixed after user spotted GEE mismatch: latest 2026-05-18 rows now classify by before/after shares and aggregate same-day lots: BFC BÁN HẾT, DXP BÁN HẾT, GEE BÁN 1 PHẦN, VIC MUA MỚI, PVP MUA MỚI. Copy/rebalance table uses latest ledger rows when available, not net holdings.

## 4. CẢNH BÁO — bug methodology cần biết

**Engine `engine.py` cũ có MTM stale bug.** `equity_curve.parquet` báo cáo yearly returns SAI BÉT (lệch tới ±124pp so với honest MTM). Terminal NAV và CAGR cumulative thì đúng, nhưng yearly returns + MaxDD + Sharpe đều sai.

**Luôn dùng `equity_curve_honest.parquet`** (output của `backtest/tools/honest_mtm.py`). KHÔNG trust `equity_curve.parquet` cho yearly metrics.

Ví dụ engine báo 2025 +17.5% trong khi MTM honest +141% (GEE rally 13k→135k bị dồn realize gain vào tuần SELL).

## 5. Data quality status

**Verified:**
- VNI 2016-2026 (2615 trading days): match 100% với VCI source
- Stock prices: 10 mã sample (VNM, HPG, VIC, VHM, MWG, MSN, GAS, FPT, CTG, VCB) — match VCI < 0.5% diff
- 48 zero-close bugs ngày 2025-05-28 đã patch (forward-fill)
- Clean cache: `.cache/backtest/history_clean/` (703 syms)

**Methodology bias (phát hiện 2026-05-27):**
- VCI điều chỉnh giá BACKWARD theo split/cổ tức nhưng KHÔNG điều chỉnh volume → trading value = adjusted_price × volume bị under-estimate cho mã có nhiều bonus shares post-T.
- Verified VNM 2018-01-08: cache 100.58k × vol 674,800 = 67.87 tỷ/ngày (computed); raw price thực ~210k → trading value thực ≈ 141.7 tỷ/ngày. Bias factor 2.1x.
- Cache vs fresh VCI: 1-3% diff tại boundary (events giữa snapshot date Feb-2026 và now). Volume identical confirm 469,320 cùng row.
- Hệ quả: threshold liquidity 5 tỷ/ngày trong universe filter loose tương đương ~2.5-3 tỷ thực cho mid-cap có bonus history.
- Probed extensively: VCI/KBS/MSN tất cả chỉ adjusted; Cafef block; VietStock có HTML không trivial parse; events() chỉ 50 records gần đây.
- Action 2026-05-27: re-fetch full 2010-2026 vào `.cache/backtest/history_2012/` (138/705 done, remainder do anh chạy local Windows). Sau khi xong → re-run Pair657 + rank_best_full để compare metrics. Handoff: `output/extend_history_2012/HANDOFF_2026_05_27.md`.

**Extension data 2010-2026 (in-progress):**
- VNI: DONE — `.cache/backtest/vnindex_daily_2012.parquet` (4171 rows, 2009-09-07 → 2026-05-27)
- Stocks: 138/705 syms — alpha A* → DHG done. Restart command: `python backtest\extend_history_2012.py --workers 6 --throttle-s 0.3 --skip-vni` (Sponsor tier) hoặc `--workers 2 --throttle-s 3.5` (Guest tier).

**Caveats:**
- Survivorship CORRECTED 2026-05-30: **509 syms** có data từ 2016-02 (verified via `.cache/backtest/history_2012/`), 523 syms ≥ 2016-07, 705 total trong cache. Số 232 ghi trước đây SAI — chỉ áp dụng cho `.cache/backtest/history_clean/` (snapshot cũ). Universe pool cho backtest 2016-2020 phải dùng `history_2012/` để có 509 mã, không phải 232.
- Path data daily 2016-2026 ĐÃ CÓ ĐỦ: `.cache/backtest/history_2012/` (705 parquet files, full universe, fetched 2026-05-27 batch). VNI từ 2009-09 trong `.cache/backtest/vnindex_daily_2012.parquet`.
- Score files `scores_2016_v4` có từ 2016-02 đến 2026-05 (124 monthly files).

**Pickle file `weekly_panel_v10_fullhistory.pkl` KHÔNG LOAD ĐƯỢC** do numpy/pandas version mismatch. Không dùng được trong session hiện tại.

## 6. Bộ lọc cổ phiếu (rank_best_full engine)

**Universe:** market cap ≥ 1,500 tỷ + avg vol 20D ≥ 5 tỷ/ngày + price ≥ 5,000 VND.

**Score factors:** 4 components, range 0-100.
- Technical (25% weight): Price > SMA20 (35đ) + Price > SMA50 (30đ) + Return 20D (20đ) + Low vol (15đ)
- Quality (30%): theo sector — bank: ROE×45 + ROA×25 + NIM×15 + CIR×15; non-financial: ROE×40 + ROA×20 + Margin×20 + DE×20
- Valuation (25%): theo sector — P/E, P/B, EV/EBIT weighted
- Catalyst (20%): theo sector — earnings growth, revenue growth, profit growth

**Composite:** 0.30×Quality + 0.25×Valuation + 0.20×Catalyst + 0.25×Technical.

**Hard gates:** liquidity fail, bank ROE<12% hoặc ROA<0.8%, non-financial D/E>400%, data gap.

**Selection:** Status BUY (composite ≥ 80), top 5-6 mã, cap 20% mỗi mã, score-proportional weight.

**Exit:** weekly_trend_break (2 tuần liên tiếp trend down), score_decay (composite < 50), gate_fail, stop_loss -12% từ entry.

**Chi tiết đầy đủ:** xem `output/pass30_hunt/BO_LOC_CHI_TIET_2026_05_23.md`

## 7. Files quan trọng

**Verdict files (đọc trước khi action):**
- `PARALLEL_G2_SELECTOR_RUNBOOK.md` — kế hoạch phase G2: selector/regime gate strict T-1/T cho target beat VNI +30pp
- `PARALLEL_BEAT_VNI30_RUNBOOK.md` — kế hoạch wave 2 theo target active mới: beat VNI +30 điểm % mỗi năm
- `PARALLEL_PASS30_RUNBOOK.md` — kế hoạch chia lane Codex/Claude song song cho mục tiêu pass30=6/6, supersede handoff Phase 28 cũ
- `output/HONEST_PURE_STOCK_VERDICT_2026_05_23.md` — pure-stock honest verdict, audit toàn bộ
- `output/pass30_hunt/FINAL_VERDICT_2026_05_23.md` — verdict sau hunt 34k configs
- `output/pass30_hunt/BO_LOC_CHI_TIET_2026_05_23.md` — chi tiết engine rank_best_full
**Live trading playbook:**
- `LIVE_TRADING_PLAYBOOK.md` — quy trình live đầy đủ: cron schedule, position size 1 tỷ, dashboard 7-sheet structure, troubleshooting


**Tools (dùng cho mọi backtest):**
- `backtest/tools/honest_mtm.py` — reconstruct NAV honest từ trades + history. **BẮT BUỘC dùng** thay vì equity_curve.parquet gốc.
- `backtest/tools/pass30_chunked.py` — grid search engine, checkpoint resumable
- `backtest/live_signal_generator.py` — live/paper-trade signal generator cho `rank_best_full` + VNI 8w overlay; xuất briefing, target portfolio, orders.
- `backtest/engine.py` — engine core (compute_target_weights, apply_rebalance, score_universe)
- `backtest/pit_scoring.py` — point-in-time scoring (no leakage)
- `run_stock_screen.py` — full scoring pipeline (compute_scores function line 338)

**Data:**
- `.cache/backtest/history_clean/` — 703 syms daily price clean (patched bugs)
- `.cache/backtest/vnindex_daily.parquet` — VNI 2016-2026 verified
- `.cache/backtest/scores_2016_v4/` — 124 monthly score files 2016-2026
- `.cache/backtest/scores_2016_v4_rank/` — ranked variant
- `.cache/backtest/bctc_cache*.pkl` — financial statements cache
- `.cache/universe.parquet` — universe stock list

**Best engine outputs:**
- `output/backtest_weekly/rank_best_full/` — best config 2016-2026
  - `config.json` — params (min_score=50, max_holdings=6, stop_loss=12%, require_buy_status=True)
  - `trades.parquet` — 265 trades 2016-2026
  - `equity_curve_honest.parquet` — NAV honest MTM (USE THIS)
  - `equity_curve.parquet` — NAV stale (DON'T USE for yearly metrics)
  - `metrics_honest.json` — verified metrics

**Pass30 hunt artifacts:**
- `output/pass30_hunt/all_results.csv` — 8100 single-engine + overlay configs
- `output/pass30_hunt/ensemble_results.csv` — 26325 ensemble configs
- `output/pass30_hunt/best_config_equity.parquet` — NAV best config + overlay

**Data refresh 2010-2026 (in-progress 2026-05-27):**
- `backtest/extend_history_2012.py` — re-fetch full universe từ VCI vào folder song song
- `.cache/backtest/history_2012/` — 138/705 syms done, alpha A→DHG
- `.cache/backtest/vnindex_daily_2012.parquet` — VNI DONE 2009-09 → 2026-05
- `output/extend_history_2012/HANDOFF_2026_05_27.md` — handoff cho local Windows run
- Lý do refresh: liquidity bias (VCI adjust price không adjust volume → trading value under-estimate cho mã có bonus history)

**Beat VNI +30pp wave artifacts:**
- `PARALLEL_G2_SELECTOR_RUNBOOK.md` — runbook active phase tiếp theo: selector/regime gate
- `PARALLEL_BEAT_VNI30_RUNBOOK.md` — runbook active target mới
- `output/pass30_parallel/beat_vni30_retarget/summary.md` — rescore 4,801 configs Codex theo target mới
- `output/pass30_parallel/beat_vni30_retarget/top_by_beat_vni30.csv` — top existing configs theo target mới
- `output/beat_vni30_parallel/codex_aggregate/summary.md` — Codex wave 2, 9,492 configs, best 5/6
- `output/beat_vni30_parallel/CODEX_WAVE2_HANDOFF_FOR_CLAUDE.md` — handoff cho Claude để làm selector/regime gate
- `backtest/beat_vni30_flexible_entry_search.py` — strict flexible-entry engine: signal Friday, execute Monday open/pullback/skip with full-calendar NAV accounting
- `backtest/beat_vni30_daily_execution_sim.py` — stricter daily cash/lot simulator with T+2.5 sell availability; current smoke still only 4/6, optimize before production claim
- `output/beat_vni30_parallel/CODEX_FLEXIBLE_VNI30_CANDIDATE_HANDOFF.md` — current VNI+30 candidate handoff, rules, audit, caveats
- `output/beat_vni30_parallel/LATENCY_REDUCTION_TPLUS_UPDATE.md` — latency reduction and T+2.5 update; dashboard candidate refreshed to T+2.5 strict metrics
- `output/beat_vni30_parallel/DASHBOARD_COPYTRADE_PNL_FIX.md` — dashboard audit/fix: copy-trade giá vốn/giá TT/P&L, non-empty candidate ledger, next Claude/Codex lanes
- `output/beat_vni30_parallel/DASHBOARD_PARALLEL_HANDOFF_2026_05_23.md` — explicit next lane split: Codex daily-lot ledger, Claude dashboard audit, Codex UI split
- `output/beat_vni30_parallel/CODEX_LANE_L_DAILY_LOT_CURRENT_CONFIG_VERDICT.md` — exact current dashboard config in daily cash/lot simulator: only 4/6, production blocked
- `output/beat_vni30_parallel/NEXT_PARALLEL_PLAN_AFTER_FLEXIBLE_CANDIDATE.md` — next Claude/Codex lanes: independent audit, robustness upgrade, dashboard polish, 2016-2021 extension data blocker
- `output/beat_vni30_parallel/g2_flexible_entry_buffer_candidate_audit/AUDIT.md` — strict audit for current flexible-entry dashboard candidate
- `output/dashboard_policies/flexible_vni30_candidate/` — dashboard policy package for VNI+30 Candidate

## 8. Commands cheat sheet

**Rerun honest MTM cho 1 engine:**
```bash
cd /sessions/<session>/mnt/stock_screening
python3 backtest/tools/honest_mtm.py output/backtest_weekly/rank_best_full
```

**Grid search resume (Phase 2 + 3):**
```bash
# state.json saved automatically, resume each call
python3 backtest/tools/pass30_chunked.py 35  # Phase 2 single-engine
python3 backtest/tools/pass30_chunked.py phase3 35  # Phase 3 ensemble
```

**Quick check best config:**
```bash
python3 -c "
import pandas as pd, json
m = json.load(open('output/backtest_weekly/rank_best_full/metrics_honest.json'))
print(json.dumps(m, indent=2, ensure_ascii=False))
"
```

**Generate live/paper-trade signal tuần hiện tại (NAV 1 tỷ):**
```bash
python3 backtest/live_signal_generator.py --nav 1000000000
```
Output mặc định: `output/live_signals/<as_of>/briefing.md`, `target_portfolio.csv`, `orders.csv`, `signal_state.json`.

**Verify data sanity (run periodically):**
```bash
# Cross-check VNI vs VCI source
python3 -c "
import pandas as pd
from vnstock import Vnstock
local = pd.read_parquet('.cache/backtest/vnindex_daily.parquet')
local['date']=pd.to_datetime(local['date'])
vci = Vnstock().stock(symbol='VNINDEX', source='VCI').quote.history(start='2016-01-01', end='2026-05-22')
vci['time']=pd.to_datetime(vci['time'])
# compare key dates
for d in ['2020-03-23','2022-04-04','2024-12-31']:
    td=pd.Timestamp(d)
    l = local[local.date==td]['close'].iloc[0] if (local.date==td).any() else None
    v = vci[vci.time==td]['close'].iloc[0] if (vci.time==td).any() else None
    print(f'{d}: local={l}, vci={v}')
"
```

## 9. Known dead-ends — đừng lặp lại

| Approach | Status | Lý do |
|---|---|---|
| Phase 26 hybrid_floor với VNI ETF + Bond Fund + Cash + Margin | REJECTED | Anh không chấp nhận ETF/bond/margin |
| Cash yield 8%/năm assumption (TGTK 24m + TCBF) | REJECTED | Anh không muốn bond; thực tế TGTK 24m VN 2026 chỉ 6-7% |
| Margin 1.05-1.15x với cost 6%/năm | REJECTED | Margin VN SSI/VPS thực tế 13-14%/năm, anh không dùng margin |
| Pass30 = 6/6 năm 2021-2026 pure stock + cash overlay | IMPOSSIBLE | Math: 2022 VNI -34%, không có short → không reach +30% |
| Phase 21 ultra cho 2016-2021 backtest | KHÔNG ÁP DỤNG | Phase 21 dữ liệu chỉ từ 2022-04 |
| `equity_curve.parquet` gốc cho yearly metrics | BUG | MTM stale, yearly returns lệch ±100pp |
| Direct weekly rule search trên candidate matrix | CHƯA ĐẠT | Codex Lane A/B2 đã tối ưu `backtest/pass30_direct_search.py`, thêm objective `beat_vni30` và H7-H11; wave 2 chạy 9,492 cấu hình, best beat VNI +30pp = 5/6 nhưng fail 2026 sâu |
| Online ML dự báo tuần kế tiếp | CHƯA ĐẠT | HGB/ExtraTrees expanding-window tạo năm bull rất cao nhưng 2022 drawdown lớn; recovered best pass30=3/6, MaxDD rất xấu |

**Supersede note 2026-05-23:** các dòng "pass30 impossible" và "direct weekly rule search chưa đạt" phản ánh trạng thái trước Codex G2 mutation. Codex G2 đã tìm được một **research hit** đạt beat VNI +30pp 6/6 và pass30 absolute 6/6, nhưng **chưa production** vì cần audit overfit/thanh khoản/slippage. Không được apply dashboard cho tới khi audit độc lập pass.

**Second supersede note 2026-05-23:** weekly close-to-close candidate vẫn bị reject production sau strict T-1/T audit. Candidate mới hợp lệ hơn là **flexible Monday entry**: signal sau Friday close, execute Monday open/pullback/skip bằng daily prices, full-calendar NAV. Candidate này đạt beat VNI +30pp 6/6 và đã được đưa vào dashboard như **conditional candidate**, nhưng vẫn cost-sensitive nên chưa được coi là robust production nếu slippage 0.30%/side hoặc liquidity floor 5 tỷ.

## 10. Open questions chờ anh confirm

1. **Target active đã đổi ngày 23/05/2026:** từ pass30 tuyệt đối sang **beat VNI +30 điểm % mỗi năm**. Codex flexible-entry candidate đã đạt 6/6 theo target này và đã được đưa vào dashboard như conditional candidate.

2. **Live setup:** dashboard copy-trade đã có VNI+30 Candidate và methodology panel. Còn cần xác nhận có dùng candidate này live với giả định 0.15%/side và min liquidity 3 tỷ hay không.

3. **Annual recalibrate:** T1/2027 rerun với data mới (theo SOP em đã propose hôm qua)?

## 11. Workflow chuẩn cho session mới

Nếu Claude/Codex được giao task mới:

1. **Đọc file này (CLAUDE.md) trước tiên** — 5 phút
2. Đọc verdict file mới nhất: `output/HONEST_PURE_STOCK_VERDICT_2026_05_23.md` và `output/pass30_hunt/FINAL_VERDICT_2026_05_23.md`
3. **TUYỆT ĐỐI** dùng `equity_curve_honest.parquet` cho yearly metrics, không dùng `equity_curve.parquet` gốc
4. Tuân thủ constraint: pure stock, no ETF/bond/margin/short
5. Strict T-1/T: signal phải có trước execution; không được dùng Friday close làm entry proxy. Monday open/pullback sau Friday signal được phép nếu kiểm bằng daily prices và full-calendar NAV.
6. Nếu chạy grid search dài: dùng `pass30_chunked.py` pattern (checkpoint resume mỗi 35s)
7. **Bash timeout 45s** — chunked execution, save state to disk, resume next call
8. Web search verify data assumptions (lãi suất, margin rate, VNI từ VCI source)
9. Honest reporting — không claim pass30=6/6 khi không đạt
10. Update file này khi có findings mới quan trọng

## 12. Session history (brief)

- 2026-05-21: Phase 19-25 — handoff Codex, target 30% yearly
- 2026-05-22: Phase 26-57 — claim breakthrough pass20=6/6 (BUG: leakage + MTM stale + assumption inflate)
- 2026-05-23 (sáng): Audit, phát hiện MTM bug, data quality issues, honest verdict CAGR 19-22%
- 2026-05-23 (chiều): Anh confirm pure stock, no ETF/bond/margin. Run massive grid 34k configs cho pass30=6/6. Confirmed mathematical impossibility. Best honest = rank_best_full + cash overlay (CAGR 19.9%, MaxDD -34%, pass30 3/6).
- 2026-05-23 (tối): Anh yêu cầu document toàn bộ context → file này.
- 2026-05-23 (Codex tiếp tục): Thêm `backtest/live_signal_generator.py` cho paper-trade/live weekly orders. Chạy thử NAV 1 tỷ với data latest 2026-05-21: overlay BULL (VNI 8w lag2 +11.95%) nhưng eligible_count=0 vì HHS BUY/PASS bị below_13w_sma; output tuần 2026-05-25 = 100% cash, no orders. Files: `output/live_signals/2026-05-21/`.
- 2026-05-23 (Codex G2): Tích hợp Claude F2 labels vào `backtest/pass30_direct_search.py` (`--label-dir`, external cluster/H11, no-future smoke). Build policy library + selector; selector không-hindsight chưa đạt. Sau đó mutation quanh breakout candidate tìm được **research hit**: beat VNI +30pp 6/6, pass30 absolute 6/6, CAGR 107.06%, MaxDD -36.68%, min edge +38.72pp. Files: `output/beat_vni30_parallel/g2_mutation_breakout/best_stock_only/`, audit `output/beat_vni30_parallel/g2_candidate_audit/AUDIT.md`, handoff `output/beat_vni30_parallel/CODEX_G2_HIT_HANDOFF_FOR_CLAUDE.md`. Dashboard vẫn blocked chờ audit overfit/thanh khoản/slippage.
- 2026-05-23 (Codex sau Claude G2-E/F/G): Implement weekly selector labels + risk-floor cash + cluster-overheat exposure reduction. Chạy safe mutation với cap 33%, min liquidity 3 tỷ, extra slippage 0.15%/side. Chọn pre-production candidate `output/beat_vni30_parallel/g2_preproduction_candidate/`: beat VNI +30pp 6/6, CAGR 71.0%, MaxDD -24.95%, min edge +30.37pp, nhưng buffer mỏng +0.37pp và fail stress 0.30%/side / min-liq 5 tỷ. Dashboard vẫn blocked chờ Claude/fresh audit.
- 2026-05-23 (Strict T-1/T audit): Theo yêu cầu anh, Codex test lại timing. Pre-production candidate fail: lag 1 weekly bar = 0/6, daily Monday execution proxy = 1/6, strict-lag mutation 601 runs best = 1/6. Root issue là weekly engine dùng Friday close làm cả signal và entry proxy. Verdict: `output/beat_vni30_parallel/G2_STRICT_AUDIT_VERDICT.md`. Không public dashboard, không thêm tab model vào dashboard cho candidate này, không chạy 2016-2021 extension vì gate 2021-2026 đã fail.
- 2026-05-23 (Flexible Monday entry retest): Theo gợi ý của anh, Codex thêm `backtest/beat_vni30_flexible_entry_search.py`: nếu Monday open <= close thứ 6 hoặc gap nhỏ thì mua open; nếu gap up lớn thì chờ tới thứ 4 xem có về vùng close thứ 6 không; test cả nhánh skip và window-close. Phát hiện và sửa thêm bug daily-entry: NAV chỉ ghi tuần có holdings, làm cash/no-fill weeks biến mất khỏi yearly return; đã sửa sang full calendar weeks. Best calendar-fixed flexible-entry đạt **5/6 beat VNI+30pp**, CAGR 68.28%, MaxDD -21.32%, nhưng vẫn fail **2024**: 2024 +23.7%, edge +10.8pp, thiếu 19.2pp so target. 2026 đã pass +45.5%, edge +37.8pp. Files: `output/beat_vni30_parallel/g2_flexible_entry_search_skip_calendar_fixed/summary.md`, `output/beat_vni30_parallel/g2_flexible_entry_2024_autopsy/summary.md`. Dashboard vẫn blocked; next lane cần xử lý 2024 regime/sector/drawdown brake, không chỉ execution price.
- 2026-05-23 (Codex redirect theo yêu cầu anh): Quay lại target pass30=6/6 trước khi apply dashboard. Thêm `backtest/pass30_direct_search.py` để search trực tiếp từ weekly candidate matrix. Đã chạy stock_only, target_forcing gross/hedge, và thử online ML expanding-window. Chưa có candidate đạt 6/6; best mới chỉ 3/6. Chưa apply vào dashboard copy-trade vì chưa đạt target.
- 2026-05-23 (Codex parallelization): Tạo `PARALLEL_PASS30_RUNBOOK.md` để chia lane Codex/Claude: Codex chạy direct pure-stock search + dashboard dry-run adapter; Claude audit phase cũ + nghiên cứu bottleneck 2022/2026. Runbook nhấn mạnh không lặp ETF/bond/margin/cash-yield/leakage/stale MTM và không apply dashboard trước khi có candidate verified pass30=6/6.
- 2026-05-23 (Codex Lane A/B): Tối ưu `backtest/pass30_direct_search.py` để chạy nhanh hơn, thêm `--out-dir`, `--objective`, status/summary/yearly/verification artifacts. Chạy 4 sweep pure-stock tổng **4,801 configs** tại `output/pass30_parallel/`; best đạt pass30=4/6, beat VNI=6/6 nhưng 2022/2026 dưới 30 nên dashboard vẫn blocked. Lane B tạo dry-run dashboard adapter spec/touchpoints tại `output/pass30_parallel/codex_lane_b/`, không sửa production dashboard.
- 2026-05-23 (retarget): Anh đổi target active sang **beat VNI +30 điểm % mỗi năm** vì 2022 quá khó dương mạnh. Codex rescore 4,801 configs: best target mới 3/6; bottleneck chuyển sang 2023/2025/2026. Tạo `PARALLEL_BEAT_VNI30_RUNBOOK.md`, thêm objective `beat_vni30` vào `backtest/pass30_direct_search.py`. Chưa apply dashboard.
- 2026-05-23 (Codex wave 2): Implement H7-H11 vào `backtest/pass30_direct_search.py` (sector cluster, defensive bear, relaxed RSI breakout, lower-liq momentum cap, asymmetric overlay), chạy thêm **9,492 configs** tại `output/beat_vni30_parallel/`. Best target mới **5/6** nhưng 2026 edge -59.9pp, MaxDD -58.5%. Kết luận: alpha từng năm tồn tại, static rule fail; cần selector/regime gate strict T-1/T. Handoff Claude: `output/beat_vni30_parallel/CODEX_WAVE2_HANDOFF_FOR_CLAUDE.md`.
- 2026-05-23 (G2 planning): Sau Claude E2/F2 xác nhận beat VNI +30pp feasible và xuất feature labels H7-H11, tạo `PARALLEL_G2_SELECTOR_RUNBOOK.md`. Phase tiếp theo chia song song: Codex import labels/build policy library/walk-forward selector/winner protection; Claude autopsy 2026 failure, tạo weekly selector labels, audit overfit run 599.
- 2026-05-23 (Flexible-entry final): Theo execution logic anh đề xuất, Codex build strict flexible-entry engine: Monday open nếu không gap mạnh, gap mạnh thì chờ 2 phiên về vùng Friday close, không về thì skip. Sau khi fix full-calendar NAV bug và thêm conditional symbol stop, best candidate `output/beat_vni30_parallel/g2_flexible_entry_hit_mutation_buffer/best_stock_only/` đạt beat VNI +30pp **6/6**, min edge +33.36pp, CAGR 72.71%, MaxDD -24.09%, cap 33%, min liquidity 3.065 tỷ, slippage 0.15%/side. Audit: `output/beat_vni30_parallel/g2_flexible_entry_buffer_candidate_audit/AUDIT.md`. Dashboard đã cập nhật `VNI+30 Candidate`, default policy, copy-trade GEE/VIC/PVP, methodology panel, browser verify no console errors. Caveat: fail stress 0.30%/side và min-liq 5 tỷ; cần Claude independent audit khi reset.
- 2026-05-23 (Latency/T+2.5): Anh yêu cầu giảm độ trễ tín hiệu và cho phép kéo dài chờ pullback miễn bán sau T+2.5. Codex thêm T+2.5-aware fill rejection + daily stop test vào flexible engine, và tạo daily cash/lot simulator. Kết quả T+2.5 strict tốt nhất `output/beat_vni30_parallel/g2_latency_tplus3_mutation_v1/best_stock_only/`: beat VNI +30pp **6/6**, CAGR 75.95%, MaxDD -24.09%, min edge +30.34pp, gap threshold 9%, min sell sessions 3, daily stop 0%. Dashboard đã refresh theo số T+2.5, screenshot `output/beat_vni30_parallel/dashboard_vni30_candidate_tplus_verified.png`. Daily cash/lot simulator smoke mới chỉ 4/6, nên production-hardening tiếp theo là tối ưu simulator này.
- 2026-05-23 (Dashboard audit/fix): Anh báo dashboard chưa update lịch sử giá và copy trade chưa thể hiện giá vốn/giá TT/P&L. Codex + explorer audit phát hiện candidate P/L hard-code 0, ledger rỗng, cache-busting cũ. Đã sửa generator/UI, tạo `trades.parquet` 627 records rồi thay bằng daily-lot ledger; `history.js` hiện aggregate còn 882 investor-facing records, copy table thêm Giá vốn/Giá TT/Lãi-lỗ, holdings show P/L theo giá 2026-05-22. Giá vốn hiện lấy từ live-fill Monday open/pullback: GEE 121.5k, VIC 226.8k, PVP 20.15k. Anh phát hiện tiếp GEE copy/net target mâu thuẫn với lịch sử bán 1 phần; đã sửa copy/rebalance table dùng ledger mới nhất và phân loại MUA MỚI/MUA THÊM/BÁN 1 PHẦN/BÁN HẾT theo before-after shares, gom các lô cùng mã/cùng ngày. Codex Lane L daily-lot audit trên đúng config dashboard chỉ đạt 4/6, fail 2024/2026; dashboard wording đã chuyển thành candidate preview, chưa production. Next lanes: Claude audit data alignment; Codex optimize daily-lot 6/6 and split UI net holdings vs rebalance orders.

- 2026-05-23 (Dashboard UX/update cleanup): Theo yêu cầu anh, Codex bỏ cột `Lệnh` khỏi bảng danh mục đang nắm giữ; bảng Orders tách `Lãi/lỗ (tr)` và `Lãi/lỗ %`, sắp lại thứ tự cột theo Giá TT → Giá vốn → Khối lượng → Giá trị → Tỷ trọng → P/L → Target/Stop/Ghi chú. Policy selector chỉ còn `VNI+30 Candidate`, NAV input hết tràn khung, bỏ nút Full refresh/Reload/Fast update. Nút `Update` giờ chạy `update_dashboard_live_data.py`: cập nhật nhanh các mã dashboard/latest orders + VNINDEX + BCTC nếu nguồn trả về; test API hoàn tất ~4.8s, giá mới nhất 2026-05-22. Thêm tab `Bộ lọc model` giải thích bộ lọc bằng tiếng Việt dễ hiểu. `history.js` hiển thị khối lượng theo lô 100; nếu scaled order <100cp thì để trống quantity/value và ghi chú `Dưới 100cp`. Browser verify: 1 policy, không horizontal overflow, tab model logic visible, order headers đúng.
- 2026-05-23 (Dashboard NAV separation + daily-lot continuation): Anh phát hiện chart tiền và danh mục model đang bị scale theo ô NAV copy-trade. Codex tách lại contract: ô `NAV (tỷ)` chỉ áp dụng cho Orders; chart/danh mục/lịch sử dùng model account bắt đầu 1 tỷ từ 2021. Browser verify: holdings hiện tại stocks 17.04 tỷ + cash 3.73 tỷ = NAV 20.76 tỷ, mọi range chart đều kết thúc ở NAV 20.76 tỷ, All range bắt đầu từ vốn gốc 1 tỷ. Tab `Bộ lọc model` đã đổi sang bộ lọc định lượng chi tiết. Codex cũng tối ưu `backtest/beat_vni30_daily_execution_sim.py` để `simulate_daily()` giảm từ ~89s xuống ~0.1s mà metric không đổi; chạy execution grid 3,528 cấu hình và daily-eval 45 weekly configs, tất cả vẫn chỉ 4/6 strict daily-lot. Handoff cho Claude review: `output/beat_vni30_parallel/CODEX_DASHBOARD_NAV_DAILYLOT_HANDOFF_FOR_CLAUDE.md`.

- 2026-05-23 (Dashboard planned Monday orders): Anh hỏi vì sao giai đoạn 02/03/2026 đến hết 04/2026 nhìn như không trade. Codex verified bằng `output/beat_vni30_parallel/g2_latency_tplus3_mutation_v1/best_stock_only/equity_curve_honest.parquet`: có trade 2026-02-02, 2026-02-23, 2026-03-02; từ 2026-03-09 đến 2026-04-27 exposure=0, target_exposure=0, NAV flat ~20.866x, tức model chủ động đứng ngoài/cầm cash, không phải mất dữ liệu. Dashboard thêm block `Dự kiến lệnh thứ 2 tới`: dùng live preview target 2026-05-25 từ `.cache/backtest/yearly_floor_candidate_matrix_live_preview.parquet`, hiện 3 lệnh mua + 2 lệnh bán cho NAV copy-trade: bán hết GEE 1,900cp/VIC 900cp, mua mới BSR 8,100cp/NAF 5,200cp, mua thêm PVP 8,200cp; lệnh làm tròn lô 100, giá tới 2026-05-22. Anh phát hiện bug planned sells ban đầu dùng target/fresh-start shares (VIC 1,300) thay vì vị thế thật từ ledger; đã sửa sang reconstruct actual copy position từ `trades.parquet` + latest model NAV, nên VIC bán 900 khớp đúng lệnh mua 900 ngày 2026-05-18. `update_dashboard_live_data.py` giờ refresh cả mã planned orders. Browser verify no dashboard console errors, planned rows visible. Caveat: rebuild live-preview matrix full-universe mất ~158s nên chưa nhét vào Update nhanh; Update nhanh chỉ refresh giá/BCTC và re-evaluate trạng thái/khối lượng theo preview matrix đã có.

- 2026-05-23 (Dashboard execution price/P&L fix): Anh phát hiện GEE ngày 2026-05-18 bán 1 phần ở giá khớp 121.5k nhưng bảng Orders đang tính lãi/lỗ theo giá thị trường 108.8k nên chỉ hiện ~8%. Codex tách `Giá TT` và `Giá thực hiện` trong cả bảng `Dự kiến thứ 2 tới` và `Lệnh mới nhất`; lệnh bán đã khớp dùng giá thực hiện để tính lãi/lỗ thực hiện, lệnh mua/đang giữ dùng giá thị trường để tính lãi/lỗ hiện tại. GEE latest row browser-verified: Giá TT 108.8k, Giá thực hiện 121.5k, Giá vốn 100.6k, Khối lượng 800, Lãi/lỗ 16.3tr, Lãi/lỗ 20.8%. Fix thêm current holdings/planned orders lấy giá vốn bình quân từ ledger lot thật (`trades.parquet`) thay vì entry snapshot mới nhất; GEE đang giữ/planned sell dùng giá vốn 100.59k. NAV input chỉ áp dụng bảng dự kiến copy-trade; bảng lệnh mới nhất là lịch sử model.

- 2026-05-23 (Daily-lot cash signal + exchange gap guard): Claude phát hiện đúng bug daily-lot simulator miss tuần cash vì signal dates lấy từ `holdings.parquet`; tuần target cash không có holdings nên simulator ôm vị thế cũ qua 2026-03/04. Codex sửa `backtest/beat_vni30_daily_execution_sim.py` để lấy signal dates từ weekly equity curve, empty target = bán về cash. Anh phát hiện wording/rule `open không vượt 9%` sai với HOSE vì trần thông thường ~7%; Codex thêm exchange-aware guard trong dashboard + daily/flexible execution: HOSE cap effective buy-gap ~6.5%, HNX/UPCoM dùng min(base gap, biên độ sàn - buffer). Retest current config sau cash fix + exchange guard: **3/6**, CAGR 73.95%, MaxDD -24.92%, fail 2023/2024/2025. Grid daily-lot tốt nhất: **4/6**, CAGR 68.70%, MaxDD -27.83%, fail 2023/2024. Verdict: dashboard vẫn **candidate preview**, chưa production copy-trade. File: `output/beat_vni30_parallel/CODEX_LANE_N_DAILY_LOT_CASH_SIGNAL_EXCHANGE_GAP_VERDICT.md`.

- 2026-05-23 (Daily-lot objective lane O): Codex thêm `backtest/beat_vni30_daily_lot_random_search.py` để search trực tiếp bằng strict daily-lot simulator đã sửa, không qua weekly proxy. Cold-start random 200 configs tại `output/beat_vni30_parallel/codex_lane_o_daily_lot_random_search_200/` chỉ đạt **1/6**, CAGR 8.26%, kém xa known candidate. Kết luận: random từ đầu không hiệu quả; lane tiếp theo nên mutate quanh known daily-lot 4/6 candidate và tập trung 2023/2024 selection/regime. Verdict: `output/beat_vni30_parallel/CODEX_LANE_O_DAILY_LOT_RANDOM_SEARCH_VERDICT.md`.

- 2026-05-23 (Overnight objective update): Anh xác nhận lại target active: **beat VNI +30 điểm % đủ 6/6 là gate bắt buộc**, nhưng sau khi đạt gate thì phải **tiếp tục tối đa hóa CAGR càng cao càng tốt**, không dừng ở cấu hình 6/6 đầu tiên. Codex sửa overnight protocol/worker/heartbeat theo objective này: nếu hit 6/6 chỉ ghi `TARGET_HIT.md`, vẫn tiếp tục mutate để đẩy CAGR tới hết 10h hoặc tới khi có `STOP`. Claude/Cowork poll mỗi 15 phút qua `output/beat_vni30_parallel/overnight_collab/`.

## 13. Contact / handoff

Nếu session bị reset hoặc anh chuyển sang Codex/AI khác:
- Project root: `C:\Users\User\Documents\Onedrive\Documents\New project 2\stock_screening\`
- Linux mount: `/sessions/<id>/mnt/stock_screening/`
- File này là single source of truth cho project status

Cập nhật file này khi:
- Có findings mới quan trọng (bug, methodology improvement)
- Anh confirm thay đổi constraint
- Best config mới được tìm thấy
- Data quality status thay đổi
