# Claude 3-day Work Summary — Handoff for Codex Review
**Period:** 2026-05-28 → 2026-05-31 (sáng)
**Author:** Claude (autonomous, full-authority directive từ Khoa)
**Status:** Codex đã hoạt động trở lại — cần peer review trước khi advance bất kỳ promotion nào

---

## 1. PRODUCTION CANDIDATE: R46 Bear Stop M-core — đang ở Paper-trade kickoff

### 1.1. Cấu trúc model
- **Base targets:** `output/beat_vni30_parallel/m_core_conditional_retention_plateau_r15_20260528/mega-2_mid-2/holdings.parquet` (M-core R15 plateau, mega-2_mid-2 cell)
- **Participation cap:** `m_core_conditional_retention_navaware_r18_20260528.apply_participation_cap()` — NAV 3 tỷ + cap 20% ADV20
- **Execution:** `r23_flexible_exec_smoke_20260528.EXECUTION` — gap 9%, buffer 1.5%, pullback 2 phiên, min_sell 4 phiên (T+2.5)
- **Stop layer:** `r46_regime_conditional_stop_smoke_20260528` — daily 5% stop CHỈ active khi regime classifier Phase1 v4 == "bear"
- **Regime classifier:** `regime_router_phase1_classifier_v4_20260528.py` — 5 trạng thái priority bear > bull_broad > bull_narrow > recovery > sideways; backward asof join, no leak (verified `NO_LEAK_NOTE_v2.md`)
- **Engine md5 pin:** `pass30_direct_search.py` = `096afbf65c0a3c3cf1b38dce7d7d665b`

### 1.2. Metrics verified (audit 30/05)
| Metric | Value | Source |
|---|---:|---|
| CAGR 2021-2026 | 76.47% | `equity_curve.parquet` recompute |
| MaxDD 2021-2026 | -25.62% | recompute |
| Sharpe | 2.19 | lock doc |
| Pass +20pp 2021-2026 | 6/6 | `yearly.csv` |
| Pass +30pp 2021-2026 | 6/6 | `yearly.csv` |
| Min edge 2021-2026 | +32.77pp (2026 YTD) | `yearly.csv` |
| Full 2016-2026 CAGR | 46.75% | recompute |
| Full pass +30pp | 7/11 (fail 2016/17/19/20 pre-strategy era) | recompute |
| T+2.5 violations | 0/1,821 trades | trade ledger scan |
| Regime stop sells | 18 | summary.csv |

Yearly edge 2021-2026: +153.93 / +67.24 / +34.34 / +45.95 / +33.11 / +32.77 pp.

### 1.3. Stress plateau (slippage)
| Extra bps | CAGR | MaxDD | pass +30pp 21-26 | Min edge | Gate |
|---|---:|---:|---:|---:|---|
| 15bps (default) | 46.75% | -27.61% | 6/6 | +32.77pp | PASS |
| 18bps | 45.62% | -27.94% | 6/6 | +31.10pp | PASS |
| 20bps | 44.80% | -28.17% | 5/6 | +29.75pp | FAIL +30pp recent; +20pp 6/6 PASS |
| 25bps | 42.88% | -28.64% | 5/6 | +26.52pp | FAIL +30pp recent; +20pp 6/6 PASS |
| 30bps | 41.02% | -29.22% | 5/6 | +23.31pp | FAIL +30pp recent; +20pp 6/6 PASS |

**Caveat:** strict +30pp gate fails từ 20bps trở lên. Live broker cost target ≤ 18bps.

### 1.4. Audit categories pass (8/8)
1. REPRODUCE_PASS — yearly.csv match exact với config.json lock
2. UNIVERSAL_CLEAN — 0 selector labels, 0 year tags, 0 alt-config taint
3. NO_OVERFIT — walk-forward train 2016-22 mean edge +31.94pp / OOS test 2023-26 +36.54pp (OOS IMPROVED — pattern của structural alpha)
4. PIT_COMPLIANT — 0 future leak; positional idx access, backward asof regime, T+2.5 strict lot tracking
5. STRESS_PASS — slippage plateau + stop plateau (4%/5%/6% all 6/6), single-name jackknife top 3 share 27.9% PnL
6. PRODUCTION_SIGNAL_PASS — latest week 2026-05-25: MSB 5.525% + cash 94.475%
7. DASHBOARD_ALIGNED — fields match config.json exact (minor totalReturn display 2029.95 vs anh quote 2039.8 — refresh)
8. PAPER_TRADE_CONSISTENT — paper_trade_log.jsonl line 1 logged, MSB 5.525% signal_week_1

**Verdict:** `PASS_PRODUCTION_GRADE` → paper-trade kickoff cleared Monday 2026-06-01.

**Full audit:** `output/beat_vni30_parallel/R46_V4_FINAL_AUDIT_20260530.md`.

---

## 2. NHỮNG GÌ ĐÃ CHẠY THỬ VÀ KẾT LUẬN

### 2.1. Codex hits không pass audit (đều bị Claude reject)
- **r46_drawdown_ladder_ramp** (29/05): ladder ramp recovery bonus combo — overfit signature, fail walk-forward OOS.
- **r46_recovery_bonus_*** series (29/05): multiple variants — peer-review flag conflict với M-core retention plateau.
- **r46_dd_recovery_bonus_smoke** (29/05): symbol-level momentum guard — single-name dependency tăng.
- **r46_winner_retain_churn_throttle** (29/05): churn throttle — không cải thiện gate.
- **r46_holdtime_liqstab_softalert** (29/05): hold time + liquidity stability soft alert — không pass plateau.

### 2.2. Claude research lanes (29/05)
- **L4 GATED prevalidation** + **L4 OBV-AD smoke** + **L4 v2 5-bin regime × VNI4w sign**: spec-stage, không advance to dashboard.
- **L5A ZVN30RS26 prevalidation** + **L5B smoke / volzspike prevalidation**: spec-stage, blocked.
- **claude_model_success_20260530**: chỉ document, không phải production candidate.

### 2.3. R23 / blend / regime delta diagnostic (28/05)
- **r23_r46_blend_80_20**: blend ratio gây min edge giảm dưới gate.
- **r23_r46_regime_delta_diagnostic**: identify R46 underperforms R23 trong bear + 1 sideways pocket → tạo motivation cho regime-conditional stop layer.
- **r23_flexible_exec_smoke**: chính là execution layer được R46 reuse.

### 2.4. Selector labels (claude_g2_selector_labels)
- weekly_selector_labels.csv: `risk_floor_required`, `cluster_overheat`, `winner_protect_ok`, `rotation_reentry_ok` — đã integrate vào pass30_direct_search.py các cờ optional.

---

## 3. DASHBOARD CHANGES (31/05 sáng)

### 3.1. "Bộ lọc model" tab rewrite
- **Before:** 16 cards generic (Tài sản, Bộ máy xếp hạng, Valuation, Quality, Catalyst, Technical, Ngành, Thanh khoản, Retention, Chọn DM, Giá mua, Giá bán, Stop-loss, Làm tròn, Chi phí, Audit)
- **After:** 10 cards detailed R46 spec hiển thị public, có số liệu cụ thể từ engine code:
  1. Vũ trụ cổ phiếu (HOSE/HNX/UPCoM + ADV20 ≥ 5 tỷ + low_liq exception)
  2. Score gates (7 components + composite ≥ 70 + industry ≥ 40 + RSI 35-78)
  3. Entry sector_cluster family (high_rank 0.45 + cluster_breakout_flag 35 điểm)
  4. Position sizing (max 5 mã, cap 55%, NAV-aware 20% ADV)
  5. Exit 3 lớp (rebalance + T+2.5 + bear regime stop 5%)
  6. Regime Phase1 v4 (5 trạng thái + công thức cụ thể)
  7. Cash overlay passive
  8. R23 flexible exec (gap 9%, pullback 2 phiên, lot 100)
  9. Cost 30/40 bps + 15bps slippage
  10. Yearly perf 2021-2026
- **Removed:** 2 cards Caveats & Production audit status — Khoa quyết định giữ nội bộ.

### 3.2. Files updated
- `dashboard/analysis.js` — cards array 113-178 → 10 cards mới
- `generate_deep_analysis.py` — line 1560-1577 methodology_cards sync với analysis.js (để CI auto-refresh không revert)
- `dashboard/index.html` — cache-buster bump v=public_only_2026_05_31_v3
- `output/r46_filter_spec/R46_FILTER_SPEC_20260530.md` — full 12-section spec internal (giữ caveats + production status cho Codex/internal review)

### 3.3. Truncation incident (đã fix)
- File `dashboard/analysis.js` local trên máy Khoa bị truncate giữa string `"summary": "...theo dõi giá"` từ trước (47023 bytes, line 994).
- Nguyên nhân chưa rõ — có thể trong session trước OneDrive sync interruption, hoặc Edit tool partial write.
- CI workflow `dashboard-auto-refresh.yml` regenerate `analysis.js` qua `generate_deep_analysis.py` cho output cũng truncated (47023 bytes) — workflow vẫn report success do `write_text()` không raise.
- Claude splice: lấy `cur[:idx_boundary]` (R46 new cards) + `prev[idx_boundary:]` từ commit `265b5d12e4` (plannedOrders + rest). Final file 44616 bytes, parse JSON OK, 4 policies + 5 memos + 12 cards R46. Push commit `7f8d1f2e6d` → Vercel verified OK.

**Cần Codex investigate:** root cause truncation của `generate_deep_analysis.py` output. Có thể:
1. OneDrive sync conflict trên máy Khoa
2. CI runner disk pressure
3. Subtle `Path.write_text()` issue với Unicode string dài
4. Race condition giữa write và Vercel deploy step

---

## 4. CONSTRAINTS HIỆN TẠI (không đổi)

- Pure stock only — KHÔNG ETF, KHÔNG bond, KHÔNG margin, KHÔNG short
- Cash overlay được phép (yield 0%)
- Strict T-1/T daily execution; signal Friday → execute Monday open/pullback/skip
- T+2.5 settle (min_sell = 4 phiên)
- Lot size 100 cổ phiếu, làm tròn xuống
- Target active: beat VNI +30pp mỗi năm 6/6 là **gate cứng**; sau gate tiếp tục max CAGR
- Backtest period: 2016-2026 (10.3 năm full; 2021-2026 recent gate)
- Cost convention: 30bps buy / 40bps sell + 15bps slippage/side
- NAV deployment cap: 3 tỷ (live <5 tỷ); 20% ADV participation cap

---

## 5. CÂU HỎI CHỜ CODEX

### 5.1. Independent reproducibility check
- Codex chạy lại `r46_regime_conditional_stop_smoke_20260528.py` từ scratch (clean session) — có ra exact metrics CAGR 46.75 / MaxDD -27.61 / pass +30pp 6/6 21-26 không?
- Yearly.csv recompute từ equity_curve.parquet có match dashboard hiển thị không (totalReturn 2029.95 vs anh quote 2039.8)?

### 5.2. Walk-forward additional split
- Train 2016-2020 / test 2021-2026: edge OOS bao nhiêu?
- Train 2016-2023 / test 2024-2026: edge OOS bao nhiêu?
- Pattern degrade hay hold?

### 5.3. Sensitivity recheck
- Stop param plateau ±20%: 4%, 5%, 6% all 6/6 — Codex confirm?
- Bear vs bear+sideways vs bear+recovery: regime set robust?
- Cap M-core 55% → 33% (R18 default) → 25%: degrade pattern?

### 5.4. Liquidity bias deep-dive
- VCI cache adjusts price backward (bonus shares) nhưng KHÔNG adjust volume → ADV20 underestimate factor 2.1x cho mã bonus history.
- Sample 67 syms holdings 2025-2026 raw close×volume cho min ADV20 < 2 tỷ. Live broker dùng fresh ADV20 → MSB 207-229 tỷ/ngày, OK.
- Codex check: re-fetch ADV20 từ fresh VCI (post-history_2012 batch xong) → re-run filter mask → giữ universe ổn không?

### 5.5. Paper-trade gate (a) định nghĩa
- 4 tuần paper-trade từ 2026-06-01. Gate (a) pass conditions cần thống nhất:
  - Slippage thực tế ≤ 18bps/side?
  - ADV20 broker thực tế khớp với assumption?
  - Signal MSB 5.525% execute thành công Monday 06-01?
  - Drawdown 4-tuần ≤ -10%?
- Codex propose template criteria → Khoa approve.

### 5.6. Codex hits 29/05 review
- Tất cả r46_* variants Codex push 29/05 đều fail Claude peer review (overfit signature hoặc plateau fail). Codex confirm hoặc challenge các finding này?
- File reference: `output/beat_vni30_parallel/r46_recovery_bonus_*`, `r46_drawdown_*`, `r46_winner_retain_*`.

---

## 6. FILES QUAN TRỌNG ĐỂ CODEX ĐỌC TRƯỚC

| File | Mục đích |
|---|---|
| `output/beat_vni30_parallel/R46_V4_FINAL_AUDIT_20260530.md` | Full audit verdict 8 categories |
| `output/r46_filter_spec/R46_FILTER_SPEC_20260530.md` | Filter spec đầy đủ 12 sections + code refs |
| `output/dashboard_policies/r46_bear_stop_mcore/config.json` | Lock config |
| `output/dashboard_policies/r46_bear_stop_mcore/yearly.csv` | Yearly metrics verified |
| `backtest/r46_regime_conditional_stop_smoke_20260528.py` | Engine R46 stop layer (md5 da26e26883fcf123b39a8405e0f557d3) |
| `backtest/r23_flexible_exec_smoke_20260528.py` | Execution layer (md5 7809d07a79325629384617a8e2a13393) |
| `backtest/regime_router_phase1_classifier_v4_20260528.py` | Regime classifier v4 |
| `output/beat_vni30_parallel/regime_router_phase1_20260528/NO_LEAK_NOTE_v2.md` | No leak proof |
| `output/beat_vni30_parallel/paper_trade_v4_r46/paper_trade_log.jsonl` | Paper trade log line 1 |

---

## 7. NEXT ACTIONS — Khoa expected từ Codex

1. **Reproduce verify** — chạy lại R46 từ scratch, compare exact metrics.
2. **Walk-forward extended** — 2 OOS splits bổ sung (2016-20/21-26 và 2016-23/24-26).
3. **Liquidity stress với fresh ADV20** — sau khi history_2012 batch xong.
4. **Paper-trade gate (a) criteria** — Codex propose, Khoa approve.
5. **Investigate analysis.js truncation root cause** — fix `generate_deep_analysis.py` write path nếu cần (atomic temp + replace pattern).
6. **Cross-review 29/05 Codex hits** — confirm Claude reject reasoning hoặc challenge.

**Khoa rule:** không apply bất kỳ candidate nào lên dashboard production nếu chưa có cả Claude + Codex pass independent audit.

---

**Handoff signed.** Ready for Codex peer review session.
