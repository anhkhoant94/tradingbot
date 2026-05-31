# R46 Bear Stop M-core — Filter Spec đầy đủ (2026-05-30)

**Mục đích file:** Trình bày toàn bộ bộ lọc của mô hình production hiện tại R46 Bear Stop M-core, đủ chi tiết để một người ngoài đọc hiểu cách rule chọn cổ phiếu, vào lệnh và thoát lệnh.

**Engine source (đã pin md5):**
- `backtest/pass30_direct_search.py` (`md5 = 096afbf65c0a3c3cf1b38dce7d7d665b`) — search engine sinh M-core base holdings (family `sector_cluster`, mutation R15).
- `backtest/m_core_conditional_retention_plateau_r15_20260528.py` — pipeline tạo M-core base holdings `mega-2_mid-2`.
- `backtest/m_core_conditional_retention_navaware_r18_20260528.py` — gắn participation cap theo NAV / ADV.
- `backtest/r23_flexible_exec_smoke_20260528.py` — quy tắc thực thi (gap, pullback, T+2.5).
- `backtest/r46_regime_conditional_stop_smoke_20260528.py` — overlay daily stop chỉ kích hoạt trong regime bear.
- `backtest/regime_router_phase1_classifier_v4_20260528.py` — classifier regime v4.

**Config pinned:** `output/dashboard_policies/r46_bear_stop_mcore/config.json`.

---

## 1. Vũ trụ cổ phiếu (Universe)

**Giải thích:** R46 không chạy trên toàn bộ HOSE/HNX/UPCoM; nó chạy trên ma trận candidate `yearly_floor_candidate_matrix` đã được lọc trước theo các tiêu chí cơ bản.

**Quy tắc cụ thể:**
- Sàn: HOSE, HNX, UPCoM (toàn thị trường có data).
- Cờ thanh khoản (`avg_value_20d_bil`): ngưỡng cứng `min_liq` ≥ 5 tỷ VND/ngày, đo bằng giá trị giao dịch trung bình 20 phiên gần nhất. Mã dưới mốc này bị loại trừ trừ khi rơi vào "nhánh thanh khoản thấp có động lượng mạnh" (xem mục 2).
- Cờ trạng thái (`status`): mặc định `any`; khi bật mode `buy_acc` thì chỉ giữ BUY/ACCUMULATE; mode `not_avoid` loại AVOID.
- Date constraint: `score_date <= date` — đảm bảo điểm số được tính trước thời điểm tín hiệu (no future leak).

**Pseudocode:**
```python
universe = matrix.where(
    score_date <= signal_date
    AND (avg_value_20d_bil >= 5.0 OR low_liq_allowed)
    AND status in allowed_statuses
)
```

**Tham chiếu code:** `pass30_direct_search.py:917-928, 979-983`.

---

## 2. Bộ lọc điểm số (Score Gates)

**Giải thích:** Mỗi cổ phiếu trong universe có 7 score thành phần và 1 composite score tổng hợp. R46 đặt sàn hard cho cả composite và industry score để loại các tên không đủ chất lượng theo BCTC + định giá.

**Score factors (range 0-100):**

| Factor | Ý nghĩa |
|---|---|
| `fa_rank_all` | Rank fundamental (ROE, ROA, biên gộp, D/E, growth) toàn thị trường |
| `mom_rank_all` | Rank momentum 4-13 tuần |
| `rs_rank_all` | Rank relative strength vs VN-Index 13 tuần |
| `high_rank_all` | Rank gần đỉnh 52 tuần |
| `flow_rank_all` | Rank money flow / OBV |
| `industry_score` | Điểm sức mạnh ngành nội bộ |
| `tech_score_base` | Composite kỹ thuật (SMA20/50, vol, ret 20D) |

**Hard gates:**
- `composite_score >= min_comp` (ngưỡng 50-80 tùy mutation; M-core R15 dùng ≥ 70).
- `industry_score >= min_industry_score` (≥ 40-50).
- `industry_rank <= industry_top_n` (mặc định top 10 ngành dẫn dắt).
- `require_hard_gate=True`: cờ `hard_gate == "PASS"` (đã pass: ROE bank ≥ 12%, ROA bank ≥ 0.8%, D/E phi ngân hàng ≤ 400%, không gap data).
- RSI14 trong khoảng `[rsi_min, rsi_max]` (mặc định 35-78), trừ trường hợp breakout exception (mã đang ở 52W high hoặc cluster breakout flag bật).

**Nhánh "thanh khoản thấp có động lượng" (lower_liq_momentum):**
- Cho phép mã `3 tỷ ≤ ADV20 < 5 tỷ/ngày` đi qua nếu:
  - `composite_score ≥ 70`, VÀ
  - `ret13 ≥ 20%` HOẶC `ret26 ≥ 30%` HOẶC `cluster_breakout_flag > 0`.

**Pseudocode:**
```python
mask = (
    liq_ok
    AND composite_score >= 70
    AND industry_score >= 40
    AND industry_rank <= 10
    AND rsi14 in [35, 78]  # hoặc breakout exception
    AND rs13 >= 0  AND ret13 >= -5%
    AND hard_gate == "PASS"
)
```

**Tham chiếu code:** `pass30_direct_search.py:940-985`.

**Composite score công thức** (`run_stock_screen.py:338`):
```
composite = 0.30 × Quality + 0.25 × Valuation + 0.20 × Catalyst + 0.25 × Technical
```
Trong đó từng nhóm có công thức riêng theo sector (bank vs phi tài chính).

---

## 3. Điều kiện MUA (Entry — family `sector_cluster`)

**Giải thích:** Sau khi qua universe + score gates, các candidate được chấm score riêng theo family `sector_cluster` để chọn top.

**Score sector_cluster:**
```
score = Σ w_feat × rank_feat
      + 0.45 × high_rank_all
      + (35 × regime_bonus_mult) × cluster_breakout_flag
      + (6 × regime_bonus_mult) × cluster_strength_4w
      + 14 × clip(ret4, -0.2, 0.6) × 100
```
- `cluster_breakout_flag = 1` nếu trong cùng industry có ≥ 2 mã đồng thời `near_high52 ≥ 0.97 AND ret4 ≥ -2%`.
- `cluster_strength_4w` = số tuần consecutive cluster duy trì breakout.

**Quy tắc one-per-industry:** nếu `one_per_industry=True`, mỗi industry chỉ giữ 1 mã có `tech_score_base` cao nhất → chống concentration sector.

**Pseudocode:**
```python
candidates = universe[mask]
candidates['score'] = sector_cluster_scoring(candidates)
if one_per_industry:
    candidates = top_per_industry(candidates, key='tech_score_base')
top_picks = candidates.sort_values('score', ascending=False).head(max_holdings)
```

**Tham chiếu code:** `pass30_direct_search.py:990-1048`.

---

## 4. Quy mô vị thế (Position Sizing)

**Giải thích:** R46 dùng concentrated tactical sizing, kết hợp cap M-core và participation cap theo NAV.

**Cap stack:**
- **Max holdings:** 5 mã (tối đa).
- **Max weight per stock (M-core cap):** 55% trên 1 ngày signal.
- **Participation cap (NAV-aware, R18):**
  - NAV deployment cap: 3 tỷ VND.
  - ADV participation: 20% của ADV20.
  - `max_weight_by_liq = (0.20 × avg_value_20d_bil) / 3.0`.
  - Final weight = `min(M-core weight, max_weight_by_liq)`.
- **Base exposure:** weighted theo target M-core, có thể tới 100% NAV; thực tế trung bình `avg_exposure` ~ 60% (cash residual cao trong giai đoạn risk-off).
- **Riskoff exposure:** khi regime bear, không force exposure floor — cash buffer có thể lên 95%+ (tuần 2026-05-25 hiện tại MSB 5.525% + cash 94.475%).

**Tham chiếu code:** `m_core_conditional_retention_navaware_r18_20260528.py:25-34`, `r46_bear_stop_mcore/config.json` (`deployment_nav_bil=3.0`, `participation_cap_adv=0.20`).

---

## 5. Điều kiện BÁN (Exit)

**Giải thích:** Exit của R46 có 3 lớp: rebalance, T+2.5 settle, và bear regime daily stop.

**5.1. Weekly rebalance:**
- Mỗi tuần thứ 2 (Monday execution) so target mới với danh mục hiện tại.
- Nếu trọng số hiện tại vượt target + tolerance 0.1% NAV → bán phần dư.
- Bán theo giá mở cửa thứ 2.

**5.2. T+2.5 settle (HOSE quy tắc):**
- Mỗi lot mua đều track `entry_idx`.
- Chỉ được bán khi `current_idx >= entry_idx + 4` (phiên), tức tối thiểu 4 phiên sau khi mua. Đây là quy tắc T+2.5 settle thực tế của HOSE.

**5.3. Bear regime daily stop (5%, chỉ kích hoạt khi regime = bear):**
- Mỗi ngày, nếu regime_today == "bear":
  - Với mỗi lot đã giữ ≥ 4 phiên (qua T+2.5):
    - `stop_px = entry_px × (1 - 0.05)`.
    - Nếu `low_today <= stop_px`: bán tại `min(open_today, stop_px)`.
- Không áp dụng stop trong các regime khác (bull_broad, bull_narrow, recovery, sideways) → giữ vị thế qua biến động ngắn hạn.

**Pseudocode:**
```python
for lot in current_lots:
    if regime_today == "bear" and lot.age >= 4 sessions:
        if low_today <= lot.entry_px * 0.95:
            sell at min(open_today, lot.entry_px * 0.95)
```

**Tham chiếu code:** `r46_regime_conditional_stop_smoke_20260528.py:137-159`.

---

## 6. Regime Gate (M-core Phase 1 v4)

**Giải thích:** Classifier regime tuần (v4) phân loại VN-Index thành 5 trạng thái dựa trên VNI return, breadth và dispersion. Regime cập nhật backward-asof (no leak).

**Features đầu vào (toàn weekly):**
- `vni_ret_4w`, `vni_ret_13w`: VNI cumulative return 4 tuần và 13 tuần.
- `breadth_top200`: tỷ trọng mã top 200 đang trên SMA50.
- `dispersion_4w`: độ phân tán return 4 tuần.
- `breadth_recovery_2w`: số tuần liên tiếp breadth tăng từ đáy.
- `mega_cap_leadership_pit`: leadership của mega cap (point-in-time).

**Quy tắc phân loại (priority order):**

| Regime | Điều kiện |
|---|---|
| `bear` | `vni_ret_13w < -5%` HOẶC (`vni_ret_4w < -8%` VÀ `breadth_top200 < 0.30`) |
| `bull_broad` | `breadth_top200 > 0.25` VÀ `vni_ret_13w > 8%` VÀ `dispersion_4w < 0.15` VÀ `vni_ret_4w > 0` |
| `bull_narrow` | `mega_cap_leadership_pit > 8%` VÀ `vni_ret_13w > 3%` VÀ `breadth_top200 < 0.50` VÀ `vni_ret_4w > 0` |
| `recovery` | `breadth_recovery_2w ≥ 1` VÀ `vni_ret_4w > 0` VÀ `vni_ret_13w < 0%` |
| `sideways` | mặc định khi không khớp các điều kiện trên |

**Coverage (538 tuần 2016-2026):**
- bull_broad: 85 tuần (15.8%)
- bull_narrow: 8 tuần (1.5%)
- recovery: 20 tuần (3.7%)
- bear: 98 tuần (18.2%)
- sideways: 327 tuần (60.8%)

**Daily map:** daily date → weekly regime gần nhất qua `pd.merge_asof(direction='backward')`. No future leak (`NO_LEAK_NOTE_v2.md`).

**Tham chiếu code:** `regime_router_phase1_classifier_v4_20260528.py:18-44`.

---

## 7. Cash overlay

**Giải thích:** R46 không có cash overlay cứng kiểu "VNI 8w return < -X% → 100% cash". Thay vào đó, cash xuất hiện tự nhiên qua:

- **M-core risk-off filter:** khi regime weak (bear/sideways có VNI 13w return âm), classifier giảm số mã đủ filter, sum target weight giảm → phần còn lại = cash.
- **Sample tuần 2026-05-25:** chỉ 1 mã (MSB) qua được filter, weight 5.525% → cash 94.475%.
- **Cash yield giả định:** 0% (không gửi TGTK/bond fund — constraint của user).
- **Không có rule "force 100% cash"** — model luôn cho phép giữ vị thế nếu có mã pass gate.

---

## 8. Rebalance & Execution

**Tần suất:**
- Signal generate: tối Chủ nhật (sau Friday close).
- Execution: Monday open hoặc pullback 2 phiên kế tiếp.

**Quy tắc thực thi (R23 flexible exec):**

| Param | Value | Ý nghĩa |
|---|---|---|
| `gap` | 0.09 | Nếu Monday open gap > 9% vs Friday close → KHÔNG mua open, chờ pullback (HOSE cap thực ~6.5% do biên độ sàn 7%). |
| `buffer` | 0.015 | Limit pullback = `Friday_close × 1.015`. Nếu trong window 2 phiên có `low ≤ limit_px` → fill tại `min(open, limit_px)`. |
| `pullback` | 2 phiên | Window chờ pullback tối đa. |
| `min_sell` | 4 phiên | T+2.5 settle — không bán trong 4 phiên sau khi mua. |

**Pseudocode:**
```python
if monday_open <= friday_close × (1 + 0.09):
    fill_at = monday_open
else:
    for day in [monday, tuesday]:
        limit_px = friday_close × 1.015
        if day.low <= limit_px:
            fill_at = min(day.open, limit_px)
            break
    else:
        skip (MISS_BUY)
```

**Settlement:** T+2.5 (HOSE quy tắc — phiên T+2 chiều tài khoản nhận tiền).

**Lot size:** 100 cổ phiếu (làm tròn xuống lô chẵn).

**Cost:**
- Buy: phí 0.15% + slippage 0.15% = **0.30%/side**.
- Sell: phí 0.15% + thuế TNCN 0.10% + slippage 0.15% = **0.40%/side**.
- Extra slippage assumption (dashboard): **15bps/side** — robust ở 15-18bps; ngoài 20bps degrade.

**Tham chiếu code:** `r23_flexible_exec_smoke_20260528.py:37-43`, `baseline_liquid_leadership_overlay_20260527.py` (engine simulate_strict_100lot).

---

## 9. Hiệu suất verified (2021-2026 strict daily-lot)

| Năm | Strategy | VN-Index | Edge (pp) | +20pp gate | +30pp gate |
|---|---:|---:|---:|---:|---:|
| 2021 | +189.65% | +35.73% | +153.93 | PASS | PASS |
| 2022 | +34.46% | -32.78% | +67.24 | PASS | PASS |
| 2023 | +46.54% | +12.20% | +34.34 | PASS | PASS |
| 2024 | +58.06% | +12.11% | +45.95 | PASS | PASS |
| 2025 | +73.97% | +40.87% | +33.11 | PASS | PASS |
| 2026 YTD | +38.46% | +5.69% | +32.77 | PASS | PASS |

**Tổng:**
- CAGR 2021-2026: **76.47%**
- MaxDD 2021-2026: **-25.62%**
- Sharpe: **2.19**
- Pass +30pp: **6/6**
- Min edge: **+32.77pp**

**Full 2016-2026:**
- CAGR: 46.75%
- MaxDD: -27.61%
- Pass +30pp: 7/11 (fail 2016/2017/2019/2020 — pre-strategy era)

---

## 10. Caveats quan trọng

1. **Stress 20bps slippage:** recent 6/6 +30pp giảm còn 5/6. Live broker phải đạt cost ≤ 18bps/side để giữ gate strict.
2. **Liquidity bias:** VCI adjust price backward không adjust volume → ADV20 từ cache under-estimate cho mã có bonus history (factor 2.1x). Live broker ADV20 cần kiểm tra thực tế trước khi vào lệnh.
3. **Single-name concentration:** max single-stock weight per-date observed = 55% (cap M-core). Trade-off của concentrated tactical model.
4. **Universe data:** matrix từ 2016-02 trở đi (509 mã có data đủ); pre-2016 không backtest được.
5. **T+2.5 strict:** 0/1,821 trades violations — engine track lot-level chặt.

---

**Verdict tổng:** PASS_PRODUCTION_GRADE — paper-trade kickoff cleared. Dashboard promotion đến copy-trade live blocked đến khi 4 tuần paper-trade gate (a) pass + anh approve bằng văn bản.

**Đầy đủ chi tiết audit:** `output/beat_vni30_parallel/R46_V4_FINAL_AUDIT_20260530.md`.
