# AGENTS.md

This file is a pointer for Codex / any AI agent reading the repo.

**Read `CLAUDE.md` in the same directory first, then `AI_SHARED_RESEARCH_LEDGER.md`.** `CLAUDE.md` contains the full project context; the ledger contains latest pass/fail decisions and "do not rerun" notes.

**Dashboard live-source rule:** before any dashboard deploy/check, read `DASHBOARD_LIVE_SOURCE_RULES.md`. The only canonical public URL is `https://ez-trading.vercel.app`; do not use the old `trading-execution-desk-khoa` URL/project. Online Ez JSON artifacts are the live source of truth; local files are only a dev snapshot unless explicitly synced/refetched.

Runtime rule from user: always optimize time/usage. Before any wide search, run a small smoke test first; expand only if the smoke test shows measurable improvement. Do not leave long grids running blindly.

Collaboration rule: Codex and Claude are peer researchers. Both may propose, run, and audit. No fixed "one builds / one audits" split. Any candidate must be cross-reviewed before robust production/dashboard promotion.

Cost convention: when project notes say "15bps", it means extra execution slippage per side. Base costs are already modeled separately in strict daily engines: 15bps buy fee, 15bps sell fee, and 10bps sell-side personal income tax.

Quick links inside `CLAUDE.md`:
- Section 2: Project mission + hard constraints (pure stock, no ETF/bond/margin/short)
- Section 4: **CRITICAL BUG warning** — engine equity_curve.parquet stale MTM
- Section 5: Data quality status (verified vs caveats)
- Section 6: Bộ lọc cổ phiếu engine `rank_best_full`
- Section 7: Files quan trọng
- Section 8: Commands cheat sheet
- Section 9: Known dead-ends — don't repeat
- Section 11: Workflow chuẩn cho session mới

User language: Vietnamese. Address as "anh" (he/him), agent self-addresses as "em".

## Current Operating Gates

Default task type is trading model research for Vietnamese equities unless anh says otherwise. Start by reading `CLAUDE.md` and the newest entries in `AI_SHARED_RESEARCH_LEDGER.md`; treat the ledger as the live source for locked models, rejected lanes, paper-trade status, and "do not rerun" notes.

Always separate four statuses: `research only`, `paper-trade`, `dashboard preview`, and `production`. Do not promote a model across statuses unless the required evidence is present in the latest verdict files.

Before any broad compute, run a smoke test on a narrow sample and report whether it improves the active gate. Expand only when the smoke result is directionally useful. Prefer checkpointed/resumable scripts for long sweeps.

For every candidate, report the same audit frame: period, universe, execution timing, cash treatment, cost convention, liquidity floor, max single-stock weight, NAV/equity source, reproduce command, pass count, CAGR, MaxDD, and failed years. If these fields are missing, treat the candidate as incomplete.

Do not trust `equity_curve.parquet` for yearly metrics when `equity_curve_honest.parquet` or a verified daily cash/lot ledger exists. If artifacts disagree, trace the ledger source before interpreting performance.

For promotion candidates, require no-leak/PIT check, realistic cost stress, liquidity stress, remove-symbol or top-contributor stress, and paper-trade gate if anh has requested live readiness. If one gate fails, explain the failed gate first before proposing more optimization.

Crypto work should move to sibling project `crypto_trading`; do not mix crypto exchange assumptions, 24/7 calendars, funding rates, leverage, or perp mechanics into this equity project.
