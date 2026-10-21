#!/usr/bin/env python3
"""Live bounce-list refresh: FVG+Swing A (no WO) / B (vs spot) + weekly watchlist.

Writes into /workspace/bounce-live/ for GitHub Pages.
Prints one JSON summary line to stdout for the 4h routine.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

TAIPEI = ZoneInfo("Asia/Taipei")
SITE_DIR = Path("/workspace/bounce-live")
STATE_DIR = SITE_DIR / "state"
LOOKBACK = 25
ATR_LEN = 14
MIN_ATR_MULT = 0.5
BOUNCE_BARS = 5

SYMBOLS = [
    ("XAUUSD", "GC=F"),
    ("JPN225", "^N225"),
    ("EURUSD", "EURUSD=X"),
    ("BTCUSD", "BTC-USD"),
    ("HK50", "^HSI"),
]

HEADER_A = [
    "id", "symbol", "date", "source", "side", "level_top", "level_bot",
    "touch_price", "wo", "wo_id",
    "fwd_1_high", "fwd_1_low", "fwd_3_high", "fwd_3_low", "fwd_5_high", "fwd_5_low",
    "bounce_atr", "bounce_ok",
]

HEADER_B = [
    "id", "symbol", "asof_date", "source", "side", "level_top", "level_bot",
    "spot_close", "wo", "wo_id", "confirm_date", "dist_to_spot",
]


def now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def today_taipei() -> date:
    return now_taipei().date()


def iso_week_monday(d: date) -> date:
    return d.fromisocalendar(d.isocalendar()[0], d.isocalendar()[1], 1)


def fmt_num(x) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or pd.isna(x))):
        return ""
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s


def rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1 / length, adjust=False).mean()


def atr_wilder(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return rma(tr, length)


def load_yahoo(ticker: str, hist_start: str, hist_end_exclusive: str) -> pd.DataFrame:
    df = yf.download(
        ticker,
        start=hist_start,
        end=hist_end_exclusive,
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(f"No data for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns=str.title)
    need = ["Open", "High", "Low", "Close"]
    for c in need:
        if c not in df.columns:
            raise RuntimeError(f"{ticker} missing {c}: {list(df.columns)}")
    df = df[need].dropna(how="any").copy()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df.sort_index()
    df["ATR"] = atr_wilder(df["High"], df["Low"], df["Close"], ATR_LEN)
    week_first_open: dict[date, float] = {}
    for ts, row in df.iterrows():
        mon = iso_week_monday(ts.date())
        if mon not in week_first_open:
            week_first_open[mon] = float(row["Open"])
    df["_wo_monday"] = [iso_week_monday(ts.date()) for ts in df.index]
    df["_wo"] = [week_first_open[m] for m in df["_wo_monday"]]
    return df


def fwd_hl(df: pd.DataFrame, touch_i: int, n: int) -> tuple[float, float]:
    lo = touch_i + 1
    hi = min(touch_i + n, len(df) - 1)
    if lo > hi or lo >= len(df):
        return float("nan"), float("nan")
    sl = df.iloc[lo : hi + 1]
    return float(sl["High"].max()), float(sl["Low"].min())


# ---------- FVG ----------
@dataclass
class FVG:
    side: str
    form_i: int
    form_date: date
    top: float
    bot: float
    filled: bool = False
    touched: bool = False
    id: str = ""


def assign_fvg_ids(fvgs: list[FVG]) -> None:
    counts: dict[tuple, int] = defaultdict(int)
    for g in fvgs:
        counts[(g.side, g.form_date)] += 1
    seen: dict[tuple, int] = defaultdict(int)
    for g in fvgs:
        key = (g.side, g.form_date)
        seen[key] += 1
        prefix = "FVG-S" if g.side == "sell" else "FVG-B"
        base = f"{prefix}-{g.form_date.strftime('%Y%m%d')}"
        g.id = base if counts[key] == 1 else f"{base}-{seen[key]}"


def detect_fvgs(df: pd.DataFrame) -> list[FVG]:
    highs = df["High"].values
    lows = df["Low"].values
    atr = df["ATR"].values
    dates = [ts.date() for ts in df.index]
    out: list[FVG] = []
    for i in range(2, len(df)):
        if np.isnan(atr[i]):
            continue
        min_gap = MIN_ATR_MULT * float(atr[i])
        sell_top = float(lows[i - 2])
        sell_bot = float(highs[i])
        if sell_top > sell_bot and (sell_top - sell_bot) >= min_gap:
            out.append(FVG("sell", i, dates[i], sell_top, sell_bot))
        buy_top = float(lows[i])
        buy_bot = float(highs[i - 2])
        if buy_top > buy_bot and (buy_top - buy_bot) >= min_gap:
            out.append(FVG("buy", i, dates[i], buy_top, buy_bot))
    assign_fvg_ids(out)
    return out


def fvg_events_no_wo(df: pd.DataFrame, symbol: str, touch_start: date, touch_end: date) -> list[dict]:
    fvgs = detect_fvgs(df)
    by_form: dict[int, list[FVG]] = defaultdict(list)
    for g in fvgs:
        by_form[g.form_i].append(g)
    highs = df["High"].values
    lows = df["Low"].values
    atr = df["ATR"].values
    dates = [ts.date() for ts in df.index]
    wos = df["_wo"].values
    wo_mons = df["_wo_monday"].tolist()
    events: list[dict] = []
    active: list[FVG] = []

    for t in range(len(df)):
        if t >= 1:
            for g in by_form.get(t - 1, []):
                if not g.filled and not g.touched:
                    active.append(g)
        active = [
            g for g in active
            if (not g.filled) and (not g.touched) and g.form_i >= (t - 1 - LOOKBACK) and g.form_i < t
        ]
        d = dates[t]
        wo = float(wos[t])
        wo_id = f"WO-{wo_mons[t].strftime('%Y%m%d')}"
        if touch_start <= d <= touch_end:
            for g in active:
                touched_now = False
                touch_price = 0.0
                if g.side == "sell" and float(highs[t]) >= g.bot:
                    touch_price = g.bot
                    touched_now = True
                elif g.side == "buy" and float(lows[t]) <= g.top:
                    touch_price = g.top
                    touched_now = True
                if not touched_now:
                    continue
                g.touched = True
                f1h, f1l = fwd_hl(df, t, 1)
                f3h, f3l = fwd_hl(df, t, 3)
                f5h, f5l = fwd_hl(df, t, 5)
                if g.side == "sell":
                    bounce = touch_price - f5l if not np.isnan(f5l) else float("nan")
                else:
                    bounce = f5h - touch_price if not np.isnan(f5h) else float("nan")
                atr_t = float(atr[t])
                bounce_ok = (not np.isnan(bounce)) and (not np.isnan(atr_t)) and (bounce >= MIN_ATR_MULT * atr_t)
                events.append({
                    "id": g.id, "symbol": symbol, "date": d.isoformat(), "source": "fvg",
                    "side": g.side, "level_top": g.top, "level_bot": g.bot,
                    "touch_price": touch_price, "wo": wo, "wo_id": wo_id,
                    "fwd_1_high": f1h, "fwd_1_low": f1l, "fwd_3_high": f3h, "fwd_3_low": f3l,
                    "fwd_5_high": f5h, "fwd_5_low": f5l, "bounce_atr": bounce,
                    "bounce_ok": "true" if bounce_ok else "false",
                })
        for g in list(active):
            if g.filled or g.form_i >= t:
                continue
            if g.side == "sell" and float(highs[t]) >= g.top:
                g.filled = True
            elif g.side == "buy" and float(lows[t]) <= g.bot:
                g.filled = True
    return events


def fvg_levels_vs_spot(df: pd.DataFrame, symbol: str, asof: date | None = None) -> list[dict]:
    fvgs = detect_fvgs(df)
    by_form: dict[int, list[FVG]] = defaultdict(list)
    for g in fvgs:
        by_form[g.form_i].append(g)
    highs = df["High"].values
    lows = df["Low"].values
    dates = [ts.date() for ts in df.index]
    wos = df["_wo"].values
    wo_mons = df["_wo_monday"].tolist()
    active: list[FVG] = []

    # Walk up to asof bar (inclusive)
    last_t = len(df) - 1
    if asof is not None:
        for i, d in enumerate(dates):
            if d <= asof:
                last_t = i
    for t in range(last_t + 1):
        if t >= 1:
            for g in by_form.get(t - 1, []):
                if not g.filled:
                    active.append(g)
        active = [
            g for g in active
            if (not g.filled) and g.form_i >= (t - 1 - LOOKBACK) and g.form_i < t
        ]
        for g in list(active):
            if g.filled or g.form_i >= t:
                continue
            if g.side == "sell" and float(highs[t]) >= g.top:
                g.filled = True
            elif g.side == "buy" and float(lows[t]) <= g.bot:
                g.filled = True
        active = [g for g in active if not g.filled]

    spot = float(df["Close"].iloc[last_t])
    asof_d = dates[last_t]
    wo = float(wos[last_t])
    wo_id = f"WO-{wo_mons[last_t].strftime('%Y%m%d')}"
    rows: list[dict] = []
    for g in active:
        if g.side == "sell" and g.bot > spot:
            dist = g.bot - spot
        elif g.side == "buy" and g.top < spot:
            dist = spot - g.top
        else:
            continue
        rows.append({
            "id": g.id, "symbol": symbol, "asof_date": asof_d.isoformat(),
            "source": "fvg", "side": g.side, "level_top": g.top, "level_bot": g.bot,
            "spot_close": spot, "wo": wo, "wo_id": wo_id,
            "confirm_date": g.form_date.isoformat(), "dist_to_spot": dist,
        })
    rows.sort(key=lambda r: (0 if r["side"] == "sell" else 1,
                             -r["level_bot"] if r["side"] == "sell" else r["level_top"]))
    return rows


# ---------- Swing ----------
@dataclass
class Swing:
    side: str  # sh | sl
    mid_i: int
    confirm_i: int
    mid_date: date
    confirm_date: date
    level: float
    touched: bool = False
    broken: bool = False
    id: str = ""


def detect_swings(df: pd.DataFrame) -> list[Swing]:
    """3-bar SH/SL. Confirm on right bar (mid+1). id = SH-/SL- + mid date."""
    highs = df["High"].values
    lows = df["Low"].values
    dates = [ts.date() for ts in df.index]
    out: list[Swing] = []
    for confirm_i in range(2, len(df)):
        mid = confirm_i - 1
        left = confirm_i - 2
        h_m, h_l, h_r = float(highs[mid]), float(highs[left]), float(highs[confirm_i])
        l_m, l_l, l_r = float(lows[mid]), float(lows[left]), float(lows[confirm_i])
        if h_m > h_l and h_m > h_r:
            out.append(Swing(
                "sh", mid, confirm_i, dates[mid], dates[confirm_i], h_m,
                id=f"SH-{dates[mid].strftime('%Y%m%d')}",
            ))
        if l_m < l_l and l_m < l_r:
            out.append(Swing(
                "sl", mid, confirm_i, dates[mid], dates[confirm_i], l_m,
                id=f"SL-{dates[mid].strftime('%Y%m%d')}",
            ))
    return out


def swing_events_no_wo(df: pd.DataFrame, symbol: str, touch_start: date, touch_end: date) -> list[dict]:
    swings = detect_swings(df)
    by_confirm: dict[int, list[Swing]] = defaultdict(list)
    for s in swings:
        by_confirm[s.confirm_i].append(s)
    highs = df["High"].values
    lows = df["Low"].values
    atr = df["ATR"].values
    dates = [ts.date() for ts in df.index]
    wos = df["_wo"].values
    wo_mons = df["_wo_monday"].tolist()
    events: list[dict] = []
    active: list[Swing] = []

    for t in range(len(df)):
        # Activate swings confirmed on previous bar (available next day)
        if t >= 1:
            for s in by_confirm.get(t - 1, []):
                if not s.touched and not s.broken:
                    active.append(s)

        # Hist A: do NOT age by LOOKBACK (swings can be touched months later if unbroken).
        # LOOKBACK only applies to B (levels vs spot display).
        active = [
            s for s in active
            if (not s.touched) and (not s.broken) and s.confirm_i < t
        ]

        d = dates[t]
        wo = float(wos[t])
        wo_id = f"WO-{wo_mons[t].strftime('%Y%m%d')}"

        # Break / touch on bar t
        still: list[Swing] = []
        for s in active:
            if s.side == "sh" and float(highs[t]) > s.level:
                # broken strictly above — but if this is also first touch (>=), record touch first
                pass
            if s.side == "sl" and float(lows[t]) < s.level:
                pass

            touched_now = False
            if touch_start <= d <= touch_end and not s.touched:
                if s.side == "sh" and float(highs[t]) >= s.level:
                    touched_now = True
                elif s.side == "sl" and float(lows[t]) <= s.level:
                    touched_now = True

            if touched_now:
                s.touched = True
                touch_price = s.level
                f1h, f1l = fwd_hl(df, t, 1)
                f3h, f3l = fwd_hl(df, t, 3)
                f5h, f5l = fwd_hl(df, t, 5)
                if s.side == "sh":
                    bounce = touch_price - f5l if not np.isnan(f5l) else float("nan")
                else:
                    bounce = f5h - touch_price if not np.isnan(f5h) else float("nan")
                atr_t = float(atr[t])
                bounce_ok = (not np.isnan(bounce)) and (not np.isnan(atr_t)) and (bounce >= MIN_ATR_MULT * atr_t)
                events.append({
                    "id": s.id, "symbol": symbol, "date": d.isoformat(), "source": "swing",
                    "side": s.side, "level_top": s.level, "level_bot": s.level,
                    "touch_price": touch_price, "wo": wo, "wo_id": wo_id,
                    "fwd_1_high": f1h, "fwd_1_low": f1l, "fwd_3_high": f3h, "fwd_3_low": f3l,
                    "fwd_5_high": f5h, "fwd_5_low": f5l, "bounce_atr": bounce,
                    "bounce_ok": "true" if bounce_ok else "false",
                })
                # after touch, drop
                continue

            # break without touch (or after failed touch window)
            if s.side == "sh" and float(highs[t]) > s.level:
                s.broken = True
                continue
            if s.side == "sl" and float(lows[t]) < s.level:
                s.broken = True
                continue
            still.append(s)
        active = still

    return events


def swing_levels_vs_spot(df: pd.DataFrame, symbol: str, asof: date | None = None) -> list[dict]:
    """Unbroken swings within Pine lookbackBars=25 of asof; SH>spot / SL<spot."""
    swings = detect_swings(df)
    dates = [ts.date() for ts in df.index]
    highs = df["High"].values
    lows = df["Low"].values
    wos = df["_wo"].values
    wo_mons = df["_wo_monday"].tolist()

    last_t = len(df) - 1
    if asof is not None:
        for i, d in enumerate(dates):
            if d <= asof:
                last_t = i
    spot = float(df["Close"].iloc[last_t])
    asof_d = dates[last_t]
    wo = float(wos[last_t])
    wo_id = f"WO-{wo_mons[last_t].strftime('%Y%m%d')}"

    rows: list[dict] = []
    for s in swings:
        # mid offset from last bar: last_t - mid_i ; Pine i=mid_off-1, mid_off in 1..lookback+1
        mid_off = last_t - s.mid_i
        if mid_off < 1 or mid_off > LOOKBACK + 1:
            continue
        if s.confirm_i > last_t:
            continue
        # unbroken from mid+1 .. last_t (strict)
        broken = False
        for t in range(s.mid_i + 1, last_t + 1):
            if s.side == "sh" and float(highs[t]) > s.level:
                broken = True
                break
            if s.side == "sl" and float(lows[t]) < s.level:
                broken = True
                break
        if broken:
            continue
        if s.side == "sh" and s.level > spot:
            dist = s.level - spot
        elif s.side == "sl" and s.level < spot:
            dist = spot - s.level
        else:
            continue
        rows.append({
            "id": s.id, "symbol": symbol, "asof_date": asof_d.isoformat(),
            "source": "swing", "side": s.side, "level_top": s.level, "level_bot": s.level,
            "spot_close": spot, "wo": wo, "wo_id": wo_id,
            "confirm_date": s.confirm_date.isoformat(), "dist_to_spot": dist,
        })
    rows.sort(key=lambda r: (r["symbol"], 0 if r["side"] == "sh" else 1, -r["level_top"]))
    return rows


def write_csv(path: Path, rows: list[dict], header: list[str]) -> None:
    num_cols = {
        "level_top", "level_bot", "touch_price", "wo", "spot_close", "dist_to_spot",
        "fwd_1_high", "fwd_1_low", "fwd_3_high", "fwd_3_low",
        "fwd_5_high", "fwd_5_low", "bounce_atr",
    }
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            out = {}
            for h in header:
                v = r.get(h, "")
                if h in num_cols:
                    out[h] = fmt_num(v) if v != "" else ""
                else:
                    out[h] = v
            w.writerow(out)


def event_key(r: dict) -> str:
    return f"{r.get('symbol','')}|{r.get('id','')}|{r.get('date','')}|{r.get('source','')}"


def is_bounce_ok(r: dict) -> bool:
    return str(r.get("bounce_ok", "")).strip().lower() in ("1", "true", "yes", "y", "ok")


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def refresh(push: bool = False, asof_override: date | None = None) -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    today = today_taipei()
    touch_end = today
    touch_start = today - timedelta(days=92)  # ~3 months
    hist_start = (touch_start - timedelta(days=80)).isoformat()  # ATR + lookback buffer
    hist_end_excl = (today + timedelta(days=1)).isoformat()  # yfinance end exclusive
    week_id = iso_week_monday(today).isoformat()
    updated_at = now_taipei().isoformat(timespec="seconds")

    all_a: list[dict] = []
    all_b: list[dict] = []
    asof_dates: list[str] = []
    errors: list[str] = []

    for sym, ticker in SYMBOLS:
        try:
            df = load_yahoo(ticker, hist_start, hist_end_excl)
        except Exception as e:
            errors.append(f"{sym}: {e}")
            continue
        # optionally truncate to asof for B alignment testing
        asof = asof_override
        rows_a_fvg = fvg_events_no_wo(df, sym, touch_start, touch_end)
        rows_a_sw = swing_events_no_wo(df, sym, touch_start, touch_end)
        rows_b_fvg = fvg_levels_vs_spot(df, sym, asof)
        rows_b_sw = swing_levels_vs_spot(df, sym, asof)
        all_a.extend(rows_a_fvg)
        all_a.extend(rows_a_sw)
        all_b.extend(rows_b_fvg)
        all_b.extend(rows_b_sw)
        if rows_b_fvg or rows_b_sw:
            asof_dates.append((rows_b_fvg or rows_b_sw)[0]["asof_date"])
        print(f"{sym}: A fvg={len(rows_a_fvg)} swing={len(rows_a_sw)} | "
              f"B fvg={len(rows_b_fvg)} swing={len(rows_b_sw)}", file=sys.stderr)

    all_a_sorted = sorted(all_a, key=lambda r: (r["date"], r["symbol"], r["source"], r["id"]))
    all_b_sorted = sorted(all_b, key=lambda r: (r["symbol"], r["source"], r["id"]))

    write_csv(SITE_DIR / "data_hist.csv", all_a_sorted, HEADER_A)
    write_csv(SITE_DIR / "data_levels.csv", all_b_sorted, HEADER_B)

    # ---- watchlist / new bounce detection ----
    bounce_rows = [r for r in all_a_sorted if is_bounce_ok(r)]
    current_keys = {event_key(r): {
        "id": r["id"], "symbol": r["symbol"], "date": r["date"],
        "source": r["source"], "side": r["side"], "touch_price": fmt_num(r["touch_price"]),
        "bounce_atr": fmt_num(r["bounce_atr"]),
    } for r in bounce_rows}

    prev_path = STATE_DIR / "last_bounce_ok.json"
    watch_path = SITE_DIR / "watchlist.json"
    prev = load_json(prev_path, None)
    watch = load_json(watch_path, {"week_id": "", "symbols": [], "updated_at": ""})

    week_reset = False
    if watch.get("week_id") != week_id:
        watch = {"week_id": week_id, "symbols": [], "updated_at": updated_at}
        week_reset = True

    first_run = prev is None or "keys" not in (prev or {})
    prev_keys = set((prev or {}).get("keys", []))

    if first_run:
        # Seed snapshot; only treat current-ISO-week bounce_ok as "new" for watchlist
        week_start = iso_week_monday(today)
        new_list = [
            current_keys[k] for k in sorted(current_keys)
            if date.fromisoformat(current_keys[k]["date"]) >= week_start
        ]
    else:
        new_list = [current_keys[k] for k in sorted(current_keys) if k not in prev_keys]

    symbols_added: list[str] = []
    for ev in new_list:
        sym = ev["symbol"]
        if sym not in watch["symbols"]:
            watch["symbols"].append(sym)
            symbols_added.append(sym)
    watch["symbols"] = sorted(set(watch["symbols"]))
    watch["week_id"] = week_id
    watch["updated_at"] = updated_at

    prev_out = {
        "updated_at": updated_at,
        "week_id": week_id,
        "keys": sorted(current_keys.keys()),
        "count": len(current_keys),
    }
    prev_path.write_text(json.dumps(prev_out, ensure_ascii=False, indent=2) + "\n")
    watch_path.write_text(json.dumps(watch, ensure_ascii=False, indent=2) + "\n")

    asof_common = max(asof_dates) if asof_dates else today.isoformat()
    meta = {
        "updated_at": updated_at,
        "asof": asof_common,
        "touch_start": touch_start.isoformat(),
        "touch_end": touch_end.isoformat(),
        "week_id": week_id,
        "hist_rows": len(all_a_sorted),
        "hist_bounce_ok": len(bounce_rows),
        "levels_rows": len(all_b_sorted),
        "new_bounces": new_list,
        "week_reset": week_reset,
        "symbols_added": symbols_added,
        "errors": errors,
        "data_source": "Yahoo Finance (XAUUSD via GC=F); not Pepperstone",
    }
    (SITE_DIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    summary = {
        "ok": True,
        "updated_at": updated_at,
        "asof": asof_common,
        "week_id": week_id,
        "week_reset": week_reset,
        "hist_rows": len(all_a_sorted),
        "hist_bounce_ok": len(bounce_rows),
        "levels_rows": len(all_b_sorted),
        "new_bounces": len(new_list),
        "symbols_added": symbols_added,
        "watch_symbols": watch["symbols"],
        "errors": errors,
        "first_run": first_run,
    }

    if push:
        try:
            _git_push_site()
            summary["pushed"] = True
        except Exception as e:
            summary["pushed"] = False
            summary["push_error"] = str(e)

    print(json.dumps(summary, ensure_ascii=False))
    return summary


def _git_push_site() -> None:
    """Commit site data files and push to origin main (Pages)."""
    subprocess.run(["git", "add", "data_hist.csv", "data_levels.csv", "watchlist.json", "meta.json",
                    "index.html", "app.js", "styles.css"], cwd=SITE_DIR, check=False)
    # state stays local (not published) — ensure .gitignore
    st = subprocess.run(["git", "status", "--porcelain"], cwd=SITE_DIR, capture_output=True, text=True)
    if not st.stdout.strip():
        return
    subprocess.run(["git", "commit", "-m", f"live refresh {now_taipei().strftime('%Y-%m-%d %H:%M %Z')}"],
                   cwd=SITE_DIR, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=SITE_DIR, check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true", help="git commit+push site data to GitHub Pages")
    ap.add_argument("--asof", default=None, help="optional YYYY-MM-DD for B snapshot")
    args = ap.parse_args()
    asof = date.fromisoformat(args.asof) if args.asof else None
    refresh(push=args.push, asof_override=asof)


if __name__ == "__main__":
    main()
