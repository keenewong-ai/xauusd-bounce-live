# xauusd-bounce-live

Live bounce list（Yahoo Finance，非 Pepperstone）for XAUUSD / JPN225 / EURUSD / BTCUSD / HK50.

- Site: https://keenewong-ai.github.io/xauusd-bounce-live/
- Hist site (untouched): https://keenewong-ai.github.io/xauusd-bounce-list/

## Refresh (every 4 hours)

```
/workspace/.venv-yf/bin/python /workspace/bounce-live/refresh_live.py --push
```

Stdout is one JSON object. Routine should notify when `new_bounces > 0` (details in `new_bounces` / `meta.json`). Weekly watchlist resets on ISO Monday (`week_id`).

State (not published): `/workspace/bounce-live/state/last_bounce_ok.json`
