# xauusd-bounce-live

Live bounce list（Yahoo Finance，非 Pepperstone）for XAUUSD / JPN225 / BTCUSD / HK50 and Pepperstone Majors + Minors (AUDUSD, EURUSD, GBPUSD, USDCAD, USDCHF, USDJPY, AUDNZD, EURNZD, GBPNZD, NZDCAD, NZDCHF, NZDJPY, NZDUSD).

- Site: https://keenewong-ai.github.io/xauusd-bounce-live/
- Hist site (untouched): https://keenewong-ai.github.io/xauusd-bounce-list/

## Refresh (every 4 hours)

```
/workspace/.venv-yf/bin/python /workspace/bounce-live/refresh_live.py --push
```

Stdout is one JSON object. Routine should notify when `new_bounces > 0` or `new_follow_ups > 0` (details in `meta.json`). Weekly watchlist resets on ISO Monday (`week_id`).

Daily bounce rules are unchanged. After `bounce_ok`, the refresh adds 1H follow-up columns on table A: `extreme_price` / `extreme_date` (touch day + at most 5 later daily bars), then 48 hourly bars after that extreme. `impulse_ok` (3 same-direction 1H candles, or move ≥ 0.5×1H ATR) and `structure_ok` (bullish HL then HH, bearish LH then LL) are counted separately; `follow_up_ok` is true only when both pass. First historical pass is a baseline (no alert flood); later completions can notify.

State (not published): `/workspace/bounce-live/state/last_bounce_ok.json`, `last_follow_up_ok.json`, `last_follow_eval.json`
