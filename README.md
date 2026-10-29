# Doomsday VOX Monitor

Watches VOX Cinemas Egypt for **Avengers: Doomsday — Infinity Vision** at
**City Centre Alexandria** on **29 Dec 2026**, and alerts your phone the moment
showtimes appear.

## How it detects tickets

Every 60s it fetches:

1. the cinema's showtimes page for the target date (`/showtimes?c=city-centre-alexandria&d=20261229`)
2. the movie page (`/movies/avengers-doomsday`)

| Signal | Alert |
|---|---|
| Doomsday + "Infinity Vision" + showtimes in the same block | **URGENT**, repeated `ALERT_REPEATS` times |
| 29 Dec appears on the movie page's date list | **URGENT**, once |
| Relevant page content changed at all | normal priority (safety net if VOX's layout differs from what the parser expects) |
| 20 fetches fail in a row (blocked or site down) | normal priority |

If VOX blocks plain HTTP requests, it falls back to a headless Chromium (Playwright).

## Setup (5 minutes)

```bash
pip install -r requirements.txt
pip install playwright && playwright install chromium   # optional fallback
cp .env.example .env    # fill in at least one channel
python monitor.py --test-notify
python monitor.py
```

**Channels:** ntfy (free, easiest; set the channel to max priority in the app
so it can sound through Do Not Disturb), Telegram, WhatsApp (CallMeBot),
Pushover (emergency alerts repeat until you tap them), Twilio SMS **and a
phone call**, Discord, email. Configure as many as you like; they all fire.

## Where to run it

- **Best:** an always-on PC, laptop, or Raspberry Pi in Egypt, checking every 60s.
- **Android phone:** install Termux, `pkg install python`, and run it there (skip Playwright).
- **GitHub Actions** (`.github/workflows/monitor.yml`): no machine needed, but it
  checks at most every 5 minutes, scheduled runs can be delayed, and VOX may block
  datacenter IPs. Add your channel credentials as repo secrets, then enable Actions.
  Use it as a backup to a local run.

## Tests

```bash
pip install pytest && python -m pytest -q
```
