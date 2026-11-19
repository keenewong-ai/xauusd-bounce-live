"""1H follow-up after a daily bounce_ok (G5–G8). Daily bounce rules stay unchanged."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

HK = ZoneInfo("Asia/Hong_Kong")
UTC = ZoneInfo("UTC")

# Map a Yahoo daily date onto 1H bars (same session rules as the group 1H script).
SESSION = {
    "XAUUSD": ("America/New_York", "cme"),
    "JPN225": ("Asia/Tokyo", "calendar"),
    "HK50": ("Asia/Hong_Kong", "calendar"),
    "BTCUSD": ("UTC", "calendar"),
}
FX_SESSION = ("Europe/London", "calendar")

# Keep this module free of refresh_live imports to avoid a cycle.
ATR_LEN = 14
MIN_ATR_MULT = 0.5


def rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False).mean()


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return rma(tr, length)


def is_bounce_ok(r: dict) -> bool:
    return str(r.get("bounce_ok", "")).strip().lower() in ("1", "true", "yes", "y", "ok")

FOLLOW_BARS_1H = 48
CONSEC_CANDLES = 3
# bullish sides: buy FVG / swing low
BULLISH_SIDES = {"buy", "sl"}


def is_bullish(side: str) -> bool:
    return str(side or "").lower() in BULLISH_SIDES


def daily_bar_index(df: pd.DataFrame, d: date) -> int | None:
    dates = [ts.date() for ts in df.index]
    for i, x in enumerate(dates):
        if x == d:
            return i
    return None


def extreme_from_daily(df: pd.DataFrame, touch_i: int, side: str) -> dict[str, Any] | None:
    """G5: touch bar + at most the next 5 daily bars. Bullish = lowest low, bearish = highest high."""
    if touch_i < 0 or touch_i >= len(df):
        return None
    end_i = min(touch_i + 5, len(df) - 1)
    sl = df.iloc[touch_i : end_i + 1]
    if sl.empty:
        return None
    bull = is_bullish(side)
    if bull:
        i = int(sl["Low"].values.argmin())
        price = float(sl["Low"].iloc[i])
    else:
        i = int(sl["High"].values.argmax())
        price = float(sl["High"].iloc[i])
    bar_i = touch_i + i
    ts = df.index[bar_i]
    return {
        "extreme_price": price,
        "extreme_date": ts.date().isoformat(),
        "extreme_i": bar_i,
    }


def load_yahoo_1h(ticker: str, start: str, end_exclusive: str) -> pd.DataFrame:
    df = yf.download(
        ticker,
        start=start,
        end=end_exclusive,
        interval="1h",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"No 1h data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns=str.title)
    need = ["Open", "High", "Low", "Close"]
    for c in need:
        if c not in df.columns:
            raise RuntimeError(f"{ticker} 1h missing {c}: {list(df.columns)}")
    df = df[need].dropna(how="any").copy()
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    df.index = idx
    df = df.sort_index()
    df["ATR"] = atr_wilder(df["High"], df["Low"], df["Close"], ATR_LEN)
    return df


def _session_of(symbol: str) -> tuple[str, str]:
    return SESSION.get(str(symbol or "").upper(), FX_SESSION)


def session_slice(h1: pd.DataFrame, extreme_d: date, symbol: str) -> pd.DataFrame:
    tz_name, rule = _session_of(symbol)
    tz = ZoneInfo(tz_name)
    idx = h1.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    local = idx.tz_convert(tz)
    if rule == "cme":
        start = datetime(extreme_d.year, extreme_d.month, extreme_d.day, 18, 0, tzinfo=tz) - timedelta(days=1)
        end = datetime(extreme_d.year, extreme_d.month, extreme_d.day, 18, 0, tzinfo=tz)
        mask = (local >= start) & (local < end)
        return h1.loc[mask]
    mask = pd.Series([ts.date() == extreme_d for ts in local], index=h1.index)
    return h1.loc[mask]


def find_extreme_1h_index(
    df_1h: pd.DataFrame,
    extreme_date: str,
    extreme_price: float,
    bullish: bool,
    symbol: str = "",
) -> int | None:
    """T0 = first 1H bar in the Yahoo daily session that printed the extreme.

    Do not search neighbouring days with low<=extreme / high>=extreme: a later
    session can trade through the level and pull T0 forward by a day.
    """
    target = date.fromisoformat(extreme_date)
    sl = session_slice(df_1h, target, symbol)
    if sl.empty:
        return None
    eps = max(abs(extreme_price) * 1e-6, 1e-8)
    if bullish:
        hits = sl.index[(sl["Low"] - extreme_price).abs() <= eps]
        ts = hits[0] if len(hits) > 0 else sl.index[int(sl["Low"].values.argmin())]
    else:
        hits = sl.index[(sl["High"] - extreme_price).abs() <= eps]
        ts = hits[0] if len(hits) > 0 else sl.index[int(sl["High"].values.argmax())]
    loc = df_1h.index.get_loc(ts)
    if isinstance(loc, slice):
        loc = loc.start
    elif hasattr(loc, "min"):
        loc = int(loc.min())
    return int(loc)


def _consec_ok(sl: pd.DataFrame, bullish: bool, n: int = CONSEC_CANDLES) -> bool:
    if len(sl) < n:
        return False
    o = sl["Open"].values
    c = sl["Close"].values
    if bullish:
        flags = c > o
    else:
        flags = c < o
    run = 0
    for f in flags:
        run = run + 1 if f else 0
        if run >= n:
            return True
    return False


def impulse_ok(window: pd.DataFrame, extreme_price: float, bullish: bool, atr: float) -> bool:
    if window is None or window.empty:
        return False
    if _consec_ok(window, bullish):
        return True
    if np.isnan(atr) or atr <= 0:
        return False
    if bullish:
        move = float(window["High"].max()) - extreme_price
    else:
        move = extreme_price - float(window["Low"].min())
    return move >= MIN_ATR_MULT * atr


def structure_follow_up(df_1h: pd.DataFrame, start_i: int, extreme_price: float, bullish: bool) -> dict[str, Any]:
    """G7–G8 on the 48 1H bars after the extreme bar. mid must be at/after start_i."""
    end_i = min(start_i + FOLLOW_BARS_1H - 1, len(df_1h) - 1)
    out = {
        "structure_ok": False,
        "sw1_time": "",
        "sw2_time": "",
        "window_complete": end_i >= start_i + FOLLOW_BARS_1H - 1,
    }
    if start_i >= len(df_1h) or start_i > end_i:
        return out

    highs = df_1h["High"].values
    lows = df_1h["Low"].values
    # Need left bar for the first mid: mid can start at start_i, left = start_i-1
    confirm_from = start_i + 1  # confirm of mid=start_i
    hl_mid = None
    hl_confirm = None
    inter_ext = None

    last_confirm = min(end_i, len(df_1h) - 1)
    for confirm_i in range(max(confirm_from, 2), last_confirm + 1):
        mid = confirm_i - 1
        left = confirm_i - 2
        if mid < start_i:
            continue
        h_m, h_l, h_r = float(highs[mid]), float(highs[left]), float(highs[confirm_i])
        l_m, l_l, l_r = float(lows[mid]), float(lows[left]), float(lows[confirm_i])

        if hl_mid is None:
            if bullish and l_m < l_l and l_m < l_r and l_m > extreme_price:
                hl_mid = mid
                hl_confirm = confirm_i
                inter_ext = float(np.max(highs[start_i : mid + 1]))
                out["sw1_time"] = _fmt_ts(df_1h.index[mid])
            elif (not bullish) and h_m > h_l and h_m > h_r and h_m < extreme_price:
                hl_mid = mid
                hl_confirm = confirm_i
                inter_ext = float(np.min(lows[start_i : mid + 1]))
                out["sw1_time"] = _fmt_ts(df_1h.index[mid])
            continue

        # After HL/LH is confirmed, look for break of the intermediate extreme
        if confirm_i <= hl_confirm:
            continue
        if bullish and float(highs[confirm_i]) >= inter_ext:
            out["structure_ok"] = True
            out["sw2_time"] = _fmt_ts(df_1h.index[confirm_i])
            return out
        if (not bullish) and float(lows[confirm_i]) <= inter_ext:
            out["structure_ok"] = True
            out["sw2_time"] = _fmt_ts(df_1h.index[confirm_i])
            return out

        # Also allow the mid/right of later bars in the window (use every bar after HL confirm)
        # The loop already walks each confirm_i as a bar index after hl_confirm.
    return out


def _fmt_ts(ts) -> str:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    t = t.tz_convert(HK)
    return t.strftime("%Y-%m-%d %H:%M HKT")


def annotate_events(events: list[dict], df_d: pd.DataFrame, df_1h: pd.DataFrame | None, err: str | None = None) -> None:
    """Fill follow-up fields on bounce events for one symbol. Non-ok rows stay empty."""
    empty = {
        "extreme_price": "",
        "extreme_date": "",
        "impulse_ok": "",
        "structure_ok": "",
        "follow_up_ok": "",
        "follow_pending": "",
        "sw1_time": "",
        "sw2_time": "",
        "follow_error": err or "",
    }
    dates = [ts.date() for ts in df_d.index]
    date_to_i = {d: i for i, d in enumerate(dates)}

    for r in events:
        for k, v in empty.items():
            r.setdefault(k, v)
        if not is_bounce_ok(r):
            continue
        if df_1h is None:
            r["follow_error"] = err or "no 1h data"
            continue
        d = date.fromisoformat(r["date"])
        touch_i = date_to_i.get(d)
        if touch_i is None:
            r["follow_error"] = "touch date not in daily bars"
            continue
        ext = extreme_from_daily(df_d, touch_i, r.get("side", ""))
        if not ext:
            r["follow_error"] = "no extreme"
            continue
        r["extreme_price"] = ext["extreme_price"]
        r["extreme_date"] = ext["extreme_date"]
        bull = is_bullish(r.get("side", ""))
        ext_i = find_extreme_1h_index(df_1h, ext["extreme_date"], ext["extreme_price"], bull, r.get("symbol", ""))
        if ext_i is None:
            r["follow_error"] = "extreme not found on 1h"
            continue
        start_i = ext_i + 1
        end_i = min(start_i + FOLLOW_BARS_1H - 1, len(df_1h) - 1)
        if start_i >= len(df_1h):
            r["follow_pending"] = "true"
            r["impulse_ok"] = "false"
            r["structure_ok"] = "false"
            r["follow_up_ok"] = "false"
            continue
        window = df_1h.iloc[start_i : end_i + 1]
        atr = float(df_1h["ATR"].iloc[ext_i]) if ext_i < len(df_1h) else float("nan")
        if np.isnan(atr) and start_i < len(df_1h):
            atr = float(df_1h["ATR"].iloc[start_i])
        imp = impulse_ok(window, ext["extreme_price"], bull, atr)
        st = structure_follow_up(df_1h, start_i, ext["extreme_price"], bull)
        pending = (not st["window_complete"]) and (not st["structure_ok"])
        r["impulse_ok"] = "true" if imp else "false"
        r["structure_ok"] = "true" if st["structure_ok"] else "false"
        # follow_up_ok requires both gates, counted separately above.
        r["follow_up_ok"] = "true" if (imp and st["structure_ok"]) else "false"
        r["follow_pending"] = "true" if pending else "false"
        r["sw1_time"] = st["sw1_time"]
        r["sw2_time"] = st["sw2_time"]
        r["follow_error"] = ""
