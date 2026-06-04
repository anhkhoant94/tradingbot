# Paper-Trade Protocol — MODEL_V4 R46_bear_stop_mcore

**Window:** 2026-06-01 (Monday) → 2026-06-29 (Monday close)
**Duration:** 4 tuần
**Lock date:** 2026-05-30
**Owner:** Lưu Anh Khoa
**Status:** **DASHBOARD PROMOTE BLOCKED.** Paper-trade là gate (a) trong 3 gate production. Không apply vào copy-trade live cho tới khi cả 3 gate pass.

## Mục đích

Xác nhận R46_bear_stop_mcore (locked V4) chạy được trên data live không leak, không drift methodology, không vi phạm T+2.5, không miss thanh khoản. 4 tuần đủ ngắn để không gây loss thật, đủ dài để bắt được ít nhất 1 chu kỳ rebalance và 1 tín hiệu BEAR regime stop (nếu xảy ra).

## Rules — execution

Rebalance frequency: **weekly Monday open**. Signal source: R46 weekly backtest extended (artifact `output/dashboard_policies/r46_bear_stop_mcore/holdings.parquet` rolled forward; signal Friday close, execution Monday open).

Execution rule R46 flexible Monday: nếu Monday open vs Friday close gap up ≤ 9%, mua tại open. Nếu gap up > 9%, đặt limit prev_close*(1+1.5%) trong tối đa 2 session sau Monday. Nếu không khớp, skip. Nếu Monday open ≤ Friday close thì mua thẳng open.

Exit rule: (i) weekly target rebalance ép re-weight về target mới; (ii) bear_regime_stop = daily 5% stop-loss, chỉ active khi Phase1 v4 daily regime classifier == "bear", min hold 4 sessions trước khi stop được phép trigger; (iii) T+2.5 protective floor: không bán bất kỳ lot nào trước session 4 từ entry, kể cả khi rebalance request hạ weight về 0.

Position sizing: theo R46 native target weights từ M-core convex sleeve. **Không equal-weight top-N** — equal-weight không match recipe locked. Nếu cần equal-weight cho lý do gì khác, phải dispatch riêng và rerun audit, không thuộc paper-trade này.

## Rules — caveat record

Bất kỳ ticker nào bị cut do liquidity floor `min_liquidity_floor_vnd = 2,000,000,000` phải log riêng vào `paper_trade_log.jsonl` field `liquidity_floor_breaches`. Nếu một mã trong R46 target có ADV20 dưới 2 tỷ/ngày tại as_of, paper-trade SKIP mã đó, NAV phần đó dồn về cash, và ghi caveat. Khi tổng hợp tuần 4, đếm tổng số mã bị skip + tổng weight bị skip.

Bất kỳ lệnh nào không khớp do gap up > 9% và không có pullback cũng phải log: ticker, target weight, intended buy date, prev_close, Monday open, max session 1-2 close.

Bất kỳ T+2.5 violation nào (nếu engine bug làm lệch hold counter) phải log severity HIGH và DỪNG paper-trade ngay, vì violation đồng nghĩa engine drift khỏi pinned md5.

## Rules — signal source integrity

Mỗi Monday checkpoint chạy `weekly_checkpoint.py` BẮT BUỘC verify pinned engine md5 trước khi mark-to-market và build next signal. Nếu md5 drift trên bất kỳ file nào trong 4 file pinned, checkpoint dừng và ghi log SEVERITY=ENGINE_DRIFT. Paper-trade tạm dừng, không generate signal mới cho tới khi anh approve hoặc rollback file.

Pinned md5 reference (cùng với `MODEL_V4_R46_LOCKED_20260530.md`):

```
backtest/r46_regime_conditional_stop_smoke_20260528.py       da26e26883fcf123b39a8405e0f557d3
backtest/r23_flexible_exec_smoke_20260528.py                 7809d07a79325629384617a8e2a13393
backtest/beat_vni30_daily_execution_sim.py                   a970366a2b203ac3fcca8c73183c1f52
backtest/baseline_liquid_leadership_overlay_20260527.py      3c0cad6d7c3f883c5c1c6dbf2531daab
```

## Rules — dashboard

**KHÔNG promote dashboard.** Trong suốt 4 tuần paper-trade, dashboard giữ nguyên candidate preview wording, không đổi default policy, không thêm tab paper-trade live. Paper-trade kết quả chỉ ghi vào `paper_trade_log.jsonl` và sẽ được tổng hợp sau ngày 2026-06-29.

## Schedule

| Date | Action |
|---|---|
| 2026-05-30 (Saturday) | Lock V4, init state, generate signal week 1 (MSB 5.525%), bootstrap checkpoint run |
| 2026-06-01 (Monday) | Week 1 execution (paper buy MSB ≤ 16,350 VND or skip), checkpoint log |
| 2026-06-08 (Monday) | Week 2 checkpoint: MTM, log, build week 2 signal |
| 2026-06-15 (Monday) | Week 3 checkpoint |
| 2026-06-22 (Monday) | Week 4 checkpoint |
| 2026-06-29 (Monday) | Window close: aggregate report, evaluate gate (a) criteria, dispatch Codex audit request (gate b) |

## Gate (a) pass criteria

1. Paper NAV cumulative return ≥ VNI cumulative return at 2026-06-29 close (edge ≥ 0pp; 4 tuần ngắn không đòi +30pp).
2. T+2.5 violations cumulative = 0.
3. Không có ticker fail liquidity floor 2 tỷ/ngày trong khoảng thời gian nắm giữ thật.
4. Mỗi tuần signal Monday reproduce được từ pinned engine md5; không có ENGINE_DRIFT log.

Nếu fail bất kỳ criteria nào, paper-trade reset: review root cause, fix, không cho phép promote dashboard.

## Gate (b) — Codex audit request (queued)

Sau 2026-06-29, gửi Codex handoff yêu cầu chạy 3 audit trên R46_bear_stop_mcore với pinned engine md5:

1. Stress slippage 25bps/side (tăng từ 15bps): pass30 segment 2021-2026 phải ≥ 5/6.
2. Min liquidity floor 5 tỷ/ngày (tăng từ 2 tỷ): pass30 segment 2021-2026 phải ≥ 5/6.
3. Remove-symbol stress: lần lượt remove top-3 contributor (top theo cumulative P&L). CAGR segment 2021-2026 phải ≥ 55%.

## Gate (c) — Dashboard promote

Chỉ khi (a) và (b) đều pass, dispatch anh approval. Nếu anh approve bằng văn bản, promote: default policy dashboard = `r46_bear_stop_mcore`, methodology panel ghi rõ "MODEL_V4 LOCKED 2026-05-30, paper-trade pass + Codex audit pass + anh approve YYYY-MM-DD".

## Out of scope

- Phase G4 research mới (large-cap overlay, foreign trade, alternative regime classifier): KHÔNG chạy trong paper-trade window. Chỉ resume sau 2026-06-29.
- Modify engine code: cấm tuyệt đối tới 2026-06-29.
- Real money execution: không. Đây là paper-trade ảo NAV 1 tỷ.
