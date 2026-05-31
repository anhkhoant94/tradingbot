"""Cophieu68.vn BCTC scraper.

Source: https://www.cophieu68.vn/quote/financial_detail.php?id=<symbol>&type=quarter

Returns two long-format DataFrames per symbol:
    - kqkd: P&L (Kết quả kinh doanh), 20 quarters Q1/2021 -> Q4/2025
    - bs:   Balance sheet (Bảng cân đối kế toán), same span

Bank balance sheets only cover Q4/2019 -> Q3/2024 — handle as known limitation.

Quarter labels in the raw HTML are "Qúy X YYYY"; we normalize to
"YYYY-Qx" for sorting and joining with the price/scoring pipeline.
"""
from __future__ import annotations

import re
import time
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE_URL = "https://www.cophieu68.vn/quote/financial_detail.php"
HEADERS = {"User-Agent": UA}
DEFAULT_DELAY_S = 0.4


def _normalize_quarter_label(label: str) -> Optional[str]:
    """Convert 'Qúy 4 2025' -> '2025-Q4'. Returns None for non-quarter columns."""
    if not isinstance(label, str):
        return None
    m = re.match(r"Qúy\s+(\d)\s+(\d{4})", label.strip())
    if not m:
        return None
    return f"{m.group(2)}-Q{m.group(1)}"


def _melt_table(df: pd.DataFrame, statement: str, symbol: str) -> pd.DataFrame:
    """Pivot the wide BCTC table to long format: symbol, statement, quarter, item, value."""
    df = df.copy()
    item_col = df.columns[0]
    quarter_cols = []
    quarter_map = {}
    for col in df.columns[1:]:
        q = _normalize_quarter_label(str(col))
        if q:
            quarter_cols.append(col)
            quarter_map[col] = q
    if not quarter_cols:
        return pd.DataFrame(columns=["symbol", "statement", "quarter", "item", "value"])
    long_df = df[[item_col] + quarter_cols].rename(columns={item_col: "item"})
    long_df = long_df.melt(id_vars="item", var_name="quarter_raw", value_name="value")
    long_df["quarter"] = long_df["quarter_raw"].map(quarter_map)
    long_df["symbol"] = symbol
    long_df["statement"] = statement
    long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce")
    long_df["item"] = long_df["item"].astype(str).str.strip()
    long_df = long_df.dropna(subset=["item", "quarter"])
    return long_df[["symbol", "statement", "quarter", "item", "value"]]


def fetch_quarterly_bctc(
    symbol: str,
    *,
    cache_dir: Optional[Path] = None,
    force: bool = False,
    timeout: int = 15,
    delay_s: float = DEFAULT_DELAY_S,
    max_retries: int = 3,
) -> Optional[pd.DataFrame]:
    """Fetch quarterly KQKD + BS for `symbol`. Returns long-format DataFrame or None on failure.

    Long-format schema: symbol, statement ('kqkd'|'bs'), quarter ('YYYY-Qx'), item (Vietnamese label), value (float).

    If cache_dir provided, reads/writes <cache_dir>/<SYMBOL>.parquet.
    """
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{symbol.upper()}.parquet"
        if cache_path.exists() and not force:
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass

    url = f"{BASE_URL}?id={symbol.lower()}&type=quarter"
    last_err = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.encoding = "utf-8"
            if r.status_code != 200 or len(r.text) < 5000:
                raise RuntimeError(f"http {r.status_code} len {len(r.text)}")
            tables = pd.read_html(StringIO(r.text))
            if len(tables) < 2:
                raise RuntimeError(f"only {len(tables)} tables in response")
            kqkd = _melt_table(tables[0], "kqkd", symbol)
            bs = _melt_table(tables[1], "bs", symbol)
            combined = pd.concat([kqkd, bs], ignore_index=True)
            if combined.empty:
                raise RuntimeError("empty parsed result")
            if cache_dir is not None:
                combined.to_parquet(cache_path, index=False, engine="fastparquet")
            if delay_s > 0:
                time.sleep(delay_s)
            return combined
        except Exception as exc:
            last_err = exc
            if attempt < max_retries - 1:
                time.sleep(2.0 * (attempt + 1))
    print(f"[cp68] {symbol} FAIL after {max_retries} attempts: {last_err}")
    return None


def fetch_universe_bctc(
    symbols: list[str],
    cache_dir: Path,
    *,
    progress_every: int = 25,
    delay_s: float = DEFAULT_DELAY_S,
) -> dict[str, str]:
    """Loop through symbols, fetching & caching. Returns status dict."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    status = {}
    t_start = time.time()
    for i, sym in enumerate(symbols, 1):
        try:
            result = fetch_quarterly_bctc(sym, cache_dir=cache_dir, delay_s=delay_s)
            status[sym] = "ok" if result is not None else "fail"
        except Exception as exc:
            status[sym] = f"err:{str(exc)[:60]}"
        if i % progress_every == 0:
            elapsed = time.time() - t_start
            eta = elapsed / i * (len(symbols) - i)
            ok = sum(1 for v in status.values() if v == "ok")
            print(
                f"[cp68] {i}/{len(symbols)} | ok={ok} | "
                f"elapsed={elapsed/60:.1f}m | eta={eta/60:.1f}m",
                flush=True,
            )
    return status


if __name__ == "__main__":
    import sys
    cache = Path(__file__).resolve().parent.parent / ".cache" / "backtest" / "cp68"
    syms = sys.argv[1:] or ["VIX", "HPG", "BID"]
    for s in syms:
        df = fetch_quarterly_bctc(s, cache_dir=cache)
        print(f"{s}: rows={0 if df is None else len(df)}")
