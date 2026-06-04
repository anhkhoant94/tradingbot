from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / ".cache" / "backtest" / "history_clean"
UNIVERSE_PATH = ROOT / ".cache" / "universe.parquet"
VNI_PATH = ROOT / ".cache" / "backtest" / "vnindex_daily.parquet"
OUT = ROOT / "output" / "beat_vni30_parallel" / "technical_t2_state_machine"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_frame(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if path.suffix.lower() == ".csv":
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
    else:
        df.to_parquet(tmp, index=False)
    tmp.replace(path)


def load_vni_daily() -> pd.DataFrame:
    vni = pd.read_parquet(VNI_PATH).copy()
    vni["date"] = pd.to_datetime(vni["date"])
    vni = vni.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    vni["close"] = pd.to_numeric(vni["close"], errors="coerce")
    base = vni["close"].shift(1)
    for window in [20, 50, 100, 200]:
        vni[f"sma{window}"] = base.rolling(window, min_periods=window).mean()
    for window, sessions in [("4w", 20), ("13w", 65), ("26w", 130)]:
        vni[f"vni_ret_{window}"] = vni["close"] / vni["close"].shift(sessions) - 1.0
    return vni


def weekly_vni_features() -> pd.DataFrame:
    vni = load_vni_daily().set_index("date")
    weekly = vni.resample("W-FRI").last().dropna(subset=["close"]).reset_index()
    weekly = weekly.rename(columns={"close": "vni_close"})
    weekly["vni_above_sma20"] = weekly["vni_close"] > weekly["sma20"]
    weekly["vni_above_sma50"] = weekly["vni_close"] > weekly["sma50"]
    weekly["vni_above_sma100"] = weekly["vni_close"] > weekly["sma100"]
    weekly["vni_above_sma200"] = weekly["vni_close"] > weekly["sma200"]
    return weekly[
        [
            "date",
            "vni_close",
            "sma20",
            "sma50",
            "sma100",
            "sma200",
            "vni_ret_4w",
            "vni_ret_13w",
            "vni_ret_26w",
            "vni_above_sma20",
            "vni_above_sma50",
            "vni_above_sma100",
            "vni_above_sma200",
        ]
    ]


def symbol_weekly_features(symbol: str, vni_weekly: pd.DataFrame) -> pd.DataFrame:
    path = HISTORY_DIR / f"{symbol}.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if df.empty:
        return pd.DataFrame()
    date_col = "time" if "time" in df.columns else "date"
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        return pd.DataFrame()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).drop_duplicates(date_col).set_index(date_col)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close", "volume"])
    if len(df) < 260:
        return pd.DataFrame()

    close_lag = df["close"].shift(1)
    value_bil = df["close"] * df["volume"] / 1_000_000.0
    df["avg_value_20d_bil"] = value_bil.shift(1).rolling(20, min_periods=15).mean()
    df["sma50"] = close_lag.rolling(50, min_periods=50).mean()
    df["sma100"] = close_lag.rolling(100, min_periods=100).mean()
    df["sma200"] = close_lag.rolling(200, min_periods=200).mean()
    df["high252_prior"] = close_lag.rolling(252, min_periods=126).max()
    df["low252_prior"] = close_lag.rolling(252, min_periods=126).min()
    df["ret_13w"] = df["close"] / df["close"].shift(65) - 1.0
    df["ret_26w"] = df["close"] / df["close"].shift(130) - 1.0

    weekly = df.resample("W-FRI").last().dropna(subset=["close"]).reset_index()
    weekly = weekly.rename(columns={date_col: "date"})
    weekly = pd.merge_asof(
        weekly.sort_values("date"),
        vni_weekly[["date", "vni_ret_13w", "vni_ret_26w"]].sort_values("date"),
        on="date",
        direction="backward",
    )
    weekly["symbol"] = symbol
    weekly["tradable"] = (weekly["avg_value_20d_bil"] >= 3.0) & (weekly["close"] >= 5.0)
    weekly["above_sma50"] = weekly["close"] > weekly["sma50"]
    weekly["above_sma200"] = weekly["close"] > weekly["sma200"]
    weekly["near_high52"] = weekly["close"] >= weekly["high252_prior"] * 0.95
    weekly["near_low52"] = weekly["close"] <= weekly["low252_prior"] * 1.05
    weekly["rs_13w"] = weekly["ret_13w"] - weekly["vni_ret_13w"]
    weekly["rs_26w"] = weekly["ret_26w"] - weekly["vni_ret_26w"]
    return weekly[
        [
            "date",
            "symbol",
            "close",
            "avg_value_20d_bil",
            "tradable",
            "above_sma50",
            "above_sma200",
            "near_high52",
            "near_low52",
            "rs_13w",
            "rs_26w",
        ]
    ]


def build_breadth_panel(vni_weekly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    universe = pd.read_parquet(UNIVERSE_PATH)
    symbols = (
        universe.loc[universe["type"].astype(str).str.lower().eq("stock"), "symbol"]
        .astype(str)
        .str.upper()
        .drop_duplicates()
        .tolist()
    )
    frames = []
    for symbol in symbols:
        frame = symbol_weekly_features(symbol, vni_weekly)
        if not frame.empty:
            frames.append(frame)
    panel = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if panel.empty:
        return panel, pd.DataFrame()
    tradable = panel[panel["tradable"]].copy()

    def qspread(series: pd.Series) -> float:
        data = series.dropna()
        if len(data) < 20:
            return np.nan
        return float(data.quantile(0.9) - data.quantile(0.1))

    breadth = tradable.groupby("date").agg(
        tradable_count=("symbol", "nunique"),
        pct_above_sma50=("above_sma50", "mean"),
        pct_above_sma200=("above_sma200", "mean"),
        pct_near_high52=("near_high52", "mean"),
        pct_near_low52=("near_low52", "mean"),
        median_liq_bil=("avg_value_20d_bil", "median"),
        rs13_dispersion=("rs_13w", qspread),
        rs26_dispersion=("rs_26w", qspread),
    ).reset_index()
    return panel, breadth


def raw_state(row: pd.Series) -> str:
    breadth50 = row["pct_above_sma50"]
    high_share = row["pct_near_high52"]
    low_share = row["pct_near_low52"]
    vni13 = row["vni_ret_13w"]
    dispersion = row["rs13_dispersion"]
    breadth_improving_2w = row["breadth50_delta_1w"] > 0 and row["breadth50_delta_2w"] > 0
    recent_weak = row["breadth50_min_8w"] < 0.35 or row["vni_ret_13w"] < 0

    risk_off = (
        (row["vni_close"] < row["sma100"] and row["vni_close"] < row["sma200"] and breadth50 < 0.35)
        or vni13 <= -0.10
        or (low_share > high_share + 0.05 and breadth50 < 0.40)
    )
    if risk_off:
        return "risk_off"

    broad = (
        row["vni_close"] > row["sma50"]
        and row["vni_close"] > row["sma100"]
        and breadth50 > 0.60
        and breadth_improving_2w
    )
    if broad:
        return "broad_trend"

    recovery = (
        row["vni_close"] > row["sma20"]
        and row["vni_close"] > row["sma50"]
        and breadth_improving_2w
        and recent_weak
    )
    if recovery:
        return "recovery"

    narrow = (
        breadth50 < 0.45
        and high_share >= 0.08
        and dispersion >= 0.25
        and vni13 > -0.10
    )
    if narrow:
        return "narrow_leadership"

    if row["vni_close"] > row["sma50"] and breadth50 >= 0.55:
        return "broad_trend"
    if row["vni_close"] > row["sma20"] and recent_weak:
        return "recovery"
    if vni13 > -0.10 and high_share >= 0.05:
        return "narrow_leadership"
    return "risk_off"


def smooth_states(raw: pd.Series, min_hold_weeks: int = 4) -> list[str]:
    states: list[str] = []
    current: str | None = None
    pending: str | None = None
    pending_count = 0
    hold_count = 0
    for value in raw.astype(str).tolist():
        if current is None:
            current = value
            hold_count = 1
            states.append(current)
            continue
        if value == current:
            pending = None
            pending_count = 0
            hold_count += 1
            states.append(current)
            continue
        if value == pending:
            pending_count += 1
        else:
            pending = value
            pending_count = 1
        if pending_count >= 2 and hold_count >= min_hold_weeks:
            current = value
            pending = None
            pending_count = 0
            hold_count = 1
        else:
            hold_count += 1
        states.append(current)
    return states


def build_state_labels() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    vni_weekly = weekly_vni_features()
    stock_panel, breadth = build_breadth_panel(vni_weekly)
    labels = pd.merge(vni_weekly, breadth, on="date", how="inner").sort_values("date").reset_index(drop=True)
    labels["breadth50_delta_1w"] = labels["pct_above_sma50"].diff()
    labels["breadth50_delta_2w"] = labels["pct_above_sma50"].diff(2)
    labels["breadth50_min_8w"] = labels["pct_above_sma50"].rolling(8, min_periods=1).min()
    labels = labels.dropna(subset=["sma200", "vni_ret_13w", "pct_above_sma50", "rs13_dispersion"]).reset_index(drop=True)
    labels["raw_state"] = labels.apply(raw_state, axis=1)
    labels["state"] = smooth_states(labels["raw_state"])
    labels["state_changed"] = labels["state"].ne(labels["state"].shift(1)).fillna(False)
    labels["year"] = labels["date"].dt.year
    return labels, stock_panel, breadth


def write_outputs(labels: pd.DataFrame, stock_panel: pd.DataFrame, breadth: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    atomic_write_frame(labels, OUT / "weekly_state_labels.parquet")
    atomic_write_frame(labels, OUT / "weekly_state_labels.csv")
    atomic_write_frame(stock_panel, OUT / "weekly_stock_technical_panel.parquet")
    atomic_write_frame(breadth, OUT / "weekly_breadth_panel.csv")

    freq = (
        labels.groupby("state")
        .agg(weeks=("date", "count"), first_date=("date", "min"), last_date=("date", "max"))
        .reset_index()
    )
    freq["share"] = freq["weeks"] / len(labels)
    atomic_write_frame(freq, OUT / "state_frequency.csv")

    by_year = labels.groupby(["year", "state"]).size().rename("weeks").reset_index()
    by_year["share"] = by_year["weeks"] / by_year.groupby("year")["weeks"].transform("sum")
    atomic_write_frame(by_year, OUT / "state_frequency_by_year.csv")

    transitions = labels.loc[labels["state_changed"], ["date", "year", "raw_state", "state"]].copy()
    atomic_write_frame(transitions, OUT / "state_transitions.csv")

    changes_by_year = labels.groupby("year")["state_changed"].sum().reset_index(name="state_changes")
    atomic_write_frame(changes_by_year, OUT / "state_changes_by_year.csv")

    audit_lines = [
        "# Technical T2 State Transition Audit",
        "",
        "Status: T2-A state labels only. No portfolio backtest yet.",
        "",
        "Data policy:",
        "- Pure price/volume only.",
        "- No BCTC.",
        "- No sector current tags.",
        "- No calendar/year/ticker rescue.",
        "- State labels are intended for next-Monday execution after signal Friday.",
        "- Daily moving averages, 52-week high/low, and volume baselines are built from lagged daily values to keep Claude CV-T1 conservative.",
        "",
        f"Weeks labeled: {len(labels):,}",
        f"Date range: {labels['date'].min().date()} to {labels['date'].max().date()}",
        f"Tradable stock panel rows: {len(stock_panel):,}",
        "",
        "## State Frequency",
        "",
        freq.to_markdown(index=False),
        "",
        "## State Changes By Year",
        "",
        changes_by_year.to_markdown(index=False),
        "",
        "## Notes",
        "",
        "- Raw states are smoothed with 2 consecutive weekly confirmations and a 4-week minimum hold before switching.",
        "- The first T2-A goal is to give Claude CV-T1/CV-T2 an auditable state-label artifact.",
        "- Portfolio testing remains blocked until these labels pass basic audit.",
    ]
    atomic_write_text(OUT / "state_transition_audit.md", "\n".join(audit_lines))
    status = {
        "updated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "track": "technical_t2_state_machine",
        "stage": "T2-A_STATE_LABELS_ONLY",
        "dashboard_status": "BLOCKED",
        "weeks_labeled": int(len(labels)),
        "date_start": str(labels["date"].min().date()),
        "date_end": str(labels["date"].max().date()),
        "state_frequency": freq.to_dict(orient="records"),
        "state_changes_by_year": changes_by_year.to_dict(orient="records"),
        "portfolio_test_status": "NOT_RUN_WAITING_FOR_CLAUDE_CV_T1_CV_T2",
    }
    atomic_write_text(OUT / "status.json", json.dumps(status, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    labels, stock_panel, breadth = build_state_labels()
    write_outputs(labels, stock_panel, breadth)
    print(json.dumps({
        "weeks_labeled": int(len(labels)),
        "date_start": str(labels["date"].min().date()),
        "date_end": str(labels["date"].max().date()),
        "states": labels["state"].value_counts().to_dict(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
