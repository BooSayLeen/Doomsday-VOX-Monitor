#!/usr/bin/env python3
"""VOX Cinemas ticket monitor: Avengers: Doomsday (Infinity Vision),
City Centre Alexandria, 26 Dec 2026. Alerts your phone when tickets drop.

Same detection as the old Spider-Man script: the cinema+movie+date filtered
showtimes page lists /booking/0032-NNNNNN links only once that date is on sale.
VOX rejects non-browser TLS, so pages are fetched with curl_cffi impersonating Chrome.

Usage:
    python monitor.py                  # loop forever (every CHECK_INTERVAL_SECONDS)
    python monitor.py --once           # single check (GitHub Actions)
    python monitor.py --test-notify    # send a test alert on every configured channel
"""
from __future__ import annotations

import argparse
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


BASE = "https://egy.voxcinemas.com"
CINEMA_SLUG = env("CINEMA_SLUG", "city-centre-alexandria")
CINEMA_CODE = env("CINEMA_CODE", "0032")
CINEMA_NAME = env("CINEMA_NAME", "City Centre Alexandria")
# Infinity Vision is listed by VOX as its own "movie" with its own slug.
MOVIE_SLUG = env("MOVIE_SLUG", "avengers-doomsday-infinity-vision")
MOVIE_NAME = env("MOVIE_NAME", "Avengers: Doomsday (Infinity Vision)")
# The regular version, watched as an early warning that the date is opening up.
EARLY_SLUG = env("EARLY_WARNING_SLUG", "avengers-doomsday")
TARGET_DATE = datetime.strptime(env("TARGET_DATE", "2026-12-26"), "%Y-%m-%d").date()
INTERVAL = int(env("CHECK_INTERVAL_SECONDS", "60"))
ALERT_REPEATS = int(env("ALERT_REPEATS", "3"))
STATE_FILE = Path(env("STATE_FILE", str(ROOT / "state.json")))

D = TARGET_DATE.strftime("%Y%m%d")
DAY = f"{TARGET_DATE:%a %d %b %Y}"
LABEL = f"{MOVIE_NAME} @ {CINEMA_NAME} on {DAY}"
POLL_URL = f"{BASE}/showtimes?c={CINEMA_SLUG}&m={MOVIE_SLUG}&d={D}"
EARLY_URL = f"{BASE}/showtimes?c={CINEMA_SLUG}&m={EARLY_SLUG}&d={D}"
BOOKING_RE = re.compile(rf"/booking/{CINEMA_CODE}-\d+")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url: str) -> str:
    from curl_cffi import requests as cffi  # Chrome TLS fingerprint gets past VOX's bot check

    r = cffi.get(url, impersonate="chrome", timeout=45)
    r.raise_for_status()
    return r.text


def bookings(html: str) -> list[str]:
    return sorted(set(BOOKING_RE.findall(html)))


# ---------------------------------------------------------------- notify ---

def notify(title: str, body: str, url: str, urgent: bool = True) -> list[str]:
    """Send on every configured channel. Returns the channels that worked."""
    import requests

    sent: list[str] = []

    def attempt(name: str, fn) -> None:
        try:
            fn()
            sent.append(name)
        except Exception as e:  # one broken channel must not block the others
            log(f"notify {name} failed: {e}")

    if env("NTFY_TOPIC"):
        def ntfy():
            headers = {"Title": title, "Priority": "5" if urgent else "3",
                       "Tags": "rotating_light,movie_camera", "Click": url,
                       "Actions": f"view, Book now, {url}"}
            if env("NTFY_EMAIL"):
                headers["Email"] = env("NTFY_EMAIL")
            requests.post(f"{env('NTFY_SERVER', 'https://ntfy.sh')}/{env('NTFY_TOPIC')}",
                          data=body.encode(), headers=headers, timeout=15).raise_for_status()
        attempt("ntfy", ntfy)

    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        attempt("telegram", lambda: requests.post(
            f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/sendMessage",
            json={"chat_id": env("TELEGRAM_CHAT_ID"), "text": f"{title}\n\n{body}\n{url}"},
            timeout=15).raise_for_status())

    if env("PUSHOVER_TOKEN") and env("PUSHOVER_USER"):
        def pushover():
            data = {"token": env("PUSHOVER_TOKEN"), "user": env("PUSHOVER_USER"),
                    "title": title, "message": body, "url": url, "url_title": "Book now",
                    "priority": 2 if urgent else 0, "sound": "siren"}
            if urgent:  # emergency priority: repeats every 60s until acknowledged
                data.update(retry=60, expire=3600)
            requests.post("https://api.pushover.net/1/messages.json", data=data,
                          timeout=15).raise_for_status()
        attempt("pushover", pushover)

    if env("DISCORD_WEBHOOK_URL"):
        attempt("discord", lambda: requests.post(
            env("DISCORD_WEBHOOK_URL"), json={"content": f"@everyone **{title}**\n{body}\n{url}"},
            timeout=15).raise_for_status())

    if env("CALLMEBOT_PHONE") and env("CALLMEBOT_APIKEY"):  # free WhatsApp messages
        attempt("whatsapp", lambda: requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": env("CALLMEBOT_PHONE"), "apikey": env("CALLMEBOT_APIKEY"),
                    "text": f"{title}\n{body}\n{url}"}, timeout=30).raise_for_status())

    sid, tok, frm, to = (env(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
                                          "TWILIO_FROM", "TWILIO_TO"))
    if sid and tok and frm and to:
        api = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
        attempt("sms", lambda: requests.post(
            f"{api}/Messages.json", auth=(sid, tok), timeout=15,
            data={"From": frm, "To": to, "Body": f"{title}: {url}"}).raise_for_status())
        if urgent and env("TWILIO_CALL", "1") == "1":
            say = "Avengers Doomsday Infinity Vision tickets are on sale. " * 3
            attempt("call", lambda: requests.post(
                f"{api}/Calls.json", auth=(sid, tok), timeout=15,
                data={"From": frm, "To": to,
                      "Twiml": f"<Response><Say>{say}</Say></Response>"}).raise_for_status())

    if env("SMTP_HOST") and env("EMAIL_TO"):
        def email():
            msg = EmailMessage()
            msg["Subject"], msg["To"] = title, env("EMAIL_TO")
            msg["From"] = env("SMTP_USER") or env("EMAIL_TO")
            msg.set_content(f"{body}\n\n{url}")
            with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", "587")), timeout=30) as s:
                s.starttls()
                if env("SMTP_USER"):
                    s.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
                s.send_message(msg)
        attempt("email", email)

    log(f"notified via: {', '.join(sent)}" if sent
        else "WARNING: no notification channel succeeded (none configured?)")
    return sent


# ---------------------------------------------------------------- main -----

def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def check(state: dict) -> bool:
    """One check cycle; mutates state. Returns True once alerting is finished."""
    if state.get("done"):
        log("already alerted; delete state to re-arm")
        return True
    try:
        found = bookings(fetch(POLL_URL))
        early = [] if state.get("early_alerted") else bookings(fetch(EARLY_URL))
    except Exception as e:
        state["errors"] = state.get("errors", 0) + 1
        log(f"fetch error #{state['errors']}: {e!r}")
        if state["errors"] == 10:
            notify("VOX monitor: checks failing",
                   f"10 failed checks in a row. Last error: {e!r}", POLL_URL, urgent=False)
        return False
    state["errors"] = 0

    if found:
        state["alerts"] = state.get("alerts", 0) + 1
        log(f"*** AVAILABLE! {len(found)} showtime(s): {found}")
        notify("DOOMSDAY INFINITY VISION TICKETS LIVE!",
               f"{LABEL}\n{len(found)} showtime(s) bookable. Book NOW!\n{BASE}{found[0]}",
               POLL_URL, urgent=True)
        state["done"] = state["alerts"] >= ALERT_REPEATS
        return state["done"]

    if early:
        state["early_alerted"] = True
        log(f"early warning: {len(early)} regular showtime(s) for {DAY}")
        notify(f"Doomsday on sale for {TARGET_DATE:%d %b} (not Infinity Vision yet)",
               f"Regular Avengers: Doomsday at {CINEMA_NAME} on {DAY} is bookable "
               f"({len(early)} showtimes). Infinity Vision may follow soon.",
               EARLY_URL, urgent=True)

    log(f"not yet: {LABEL}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="VOX ticket monitor")
    ap.add_argument("--once", action="store_true", help="run a single check and exit")
    ap.add_argument("--test-notify", action="store_true", help="send a test alert and exit")
    args = ap.parse_args()

    if args.test_notify:
        ok = notify("VOX monitor test", f"If you see this, alerts work.\nWatching: {LABEL}",
                    POLL_URL, urgent=False)
        return 0 if ok else 1

    log(f"watching {POLL_URL}")
    while True:
        state = load_state()
        done = check(state)
        save_state(state)
        if args.once or done:
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
