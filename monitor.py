#!/usr/bin/env python3
"""Watch VOX Cinemas Egypt for Avengers: Doomsday (Infinity Vision) tickets
at City Centre Alexandria on a given date, and alert your phone.

Usage:
    python monitor.py                 # loop forever (default: every 60s)
    python monitor.py --once          # single check (for cron / GitHub Actions)
    python monitor.py --test-notify   # send a test alert on every configured channel
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------- config ---

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
    return os.environ.get(name, default).strip()


BASE_URL = env("VOX_BASE_URL", "https://egy.voxcinemas.com")
CINEMA_SLUG = env("CINEMA_SLUG", "city-centre-alexandria")
CINEMA_NAME = env("CINEMA_NAME", "City Centre Alexandria")
MOVIE_SLUG = env("MOVIE_SLUG", "avengers-doomsday")
MOVIE_KEYWORD = env("MOVIE_KEYWORD", "Doomsday")
EXPERIENCE = env("EXPERIENCE", "Infinity Vision")
TARGET_DATE = datetime.strptime(env("TARGET_DATE", "2026-12-29"), "%Y-%m-%d").date()
INTERVAL = int(env("CHECK_INTERVAL_SECONDS", "60"))
FETCH_MODE = env("FETCH_MODE", "auto")  # auto | requests | browser
NOTIFY_ON_CHANGE = env("NOTIFY_ON_CHANGE", "1") == "1"
ALERT_REPEATS = int(env("ALERT_REPEATS", "3"))
STATE_FILE = Path(env("STATE_FILE", str(ROOT / "state.json")))

DATE_COMPACT = TARGET_DATE.strftime("%Y%m%d")
SHOWTIMES_URL = env("SHOWTIMES_URL", f"{BASE_URL}/showtimes?c={CINEMA_SLUG}&d={DATE_COMPACT}")
MOVIE_URL = env("MOVIE_URL", f"{BASE_URL}/movies/{MOVIE_SLUG}")
BOOKING_URL = SHOWTIMES_URL

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TIME_RE = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\s*(?:[AaPp][Mm])?\b")


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- fetch ----

def looks_blocked(status: int, html: str) -> bool:
    if status in (403, 429, 503):
        return True
    low = html[:5000].lower()
    return any(s in low for s in ("just a moment", "cf-challenge", "access denied",
                                  "captcha", "request unsuccessful"))


def fetch_requests(url: str) -> tuple[int, str]:
    r = requests.get(url, headers=HEADERS, timeout=30)
    return r.status_code, r.text


def fetch_browser(url: str) -> tuple[int, str]:
    from playwright.sync_api import sync_playwright  # optional dependency

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=HEADERS["User-Agent"], locale="en-US")
        resp = page.goto(url, wait_until="networkidle", timeout=60000)
        html = page.content()
        browser.close()
        return (resp.status if resp else 0), html


def fetch(url: str) -> str:
    if FETCH_MODE == "browser":
        status, html = fetch_browser(url)
    else:
        status, html = fetch_requests(url)
        if FETCH_MODE == "auto" and looks_blocked(status, html):
            try:
                import playwright  # noqa: F401
            except ImportError:
                raise RuntimeError(f"blocked (HTTP {status}) and Playwright isn't installed")
            log(f"requests got {status}/blocked for {url}, retrying with headless browser")
            status, html = fetch_browser(url)
    if status >= 400:
        raise RuntimeError(f"HTTP {status} for {url}")
    return html


# ---------------------------------------------------------------- parse ----

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def find_showtimes(html: str) -> list[str]:
    """Return showtimes for MOVIE_KEYWORD + EXPERIENCE found in the page.

    VOX markup is not guaranteed, so this works structurally: for every element
    that mentions the movie, climb to the smallest ancestor block that also
    mentions the experience, and collect times / booking links inside it.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    movie, exp = norm(MOVIE_KEYWORD), norm(EXPERIENCE)
    found: set[str] = set()

    for node in soup.find_all(string=lambda t: t and movie in norm(t)):
        title = node.parent
        sig = (title.name, tuple(title.get("class") or ()))
        block = title
        for _ in range(12):
            if block.parent is None or exp in norm(block.get_text(" ")):
                break
            block = block.parent
        if exp not in norm(block.get_text(" ")):
            continue
        # If the block holds other elements shaped like our title (other movies'
        # headings), we climbed into a multi-movie list: the match isn't ours.
        siblings = [t for t in block.find_all(sig[0]) if tuple(t.get("class") or ()) == sig[1]]
        if (sig[1] or sig[0] in ("h1", "h2", "h3", "h4", "h5")) and any(movie not in norm(t.get_text(" ")) for t in siblings):
            continue
        # Narrow to the experience sub-section when the movie block lists several.
        scope = block
        for el in block.find_all(string=lambda t: t and exp in norm(t)):
            sub = el.parent
            for _ in range(4):
                if sub.parent is None or sub.parent is block:
                    break
                if TIME_RE.search(sub.get_text(" ")) or sub.find("a", href=True):
                    break
                sub = sub.parent
            scope = sub
            times = TIME_RE.findall(scope.get_text(" "))
            found.update(t.strip() for t in times)
            for a in scope.find_all("a", href=True):
                if "book" in a["href"].lower() or TIME_RE.search(a.get_text(" ")):
                    found.add(a.get_text(" ").strip() or a["href"])
    return sorted(found)


def relevant_fingerprint(html: str) -> str:
    """Hash of the parts of the page that matter, ignoring layout noise."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    keys = (norm(MOVIE_KEYWORD), norm(EXPERIENCE), norm(CINEMA_NAME))
    lines = [norm(l) for l in soup.get_text("\n").splitlines()]
    hits = sorted({l for l in lines if l and any(k in l for k in keys)})
    hrefs = sorted({a["href"] for a in soup.find_all("a", href=True)
                    if DATE_COMPACT in a["href"] or "book" in a["href"].lower()})
    return hashlib.sha256(json.dumps([hits, hrefs]).encode()).hexdigest()


def target_date_listed(html: str) -> bool:
    d = TARGET_DATE
    pats = [DATE_COMPACT, d.isoformat(), f"{d.day} {d:%b}", f"{d:%b} {d.day}", f"{d.day} {d:%B}"]
    low = html.lower()
    return any(p.lower() in low for p in pats)


# ---------------------------------------------------------------- notify ---

def notify(title: str, body: str, url: str, urgent: bool = True) -> list[str]:
    """Send on every configured channel. Returns the list of channels that worked."""
    sent: list[str] = []

    def attempt(name: str, fn) -> None:
        try:
            fn()
            sent.append(name)
        except Exception as e:  # one broken channel must not block the others
            log(f"notify {name} failed: {e}")

    if env("NTFY_TOPIC"):
        def ntfy():
            server = env("NTFY_SERVER", "https://ntfy.sh")
            r = requests.post(f"{server}/{env('NTFY_TOPIC')}", data=body.encode(), timeout=15,
                              headers={"Title": title.encode("ascii", "ignore").decode().strip(),
                                       "Priority": "5" if urgent else "3",
                                       "Tags": "rotating_light,movie_camera",
                                       "Click": url,
                                       "Actions": f"view, Book now, {url}",
                                       **({"Email": env("NTFY_EMAIL")} if env("NTFY_EMAIL") else {})})
            r.raise_for_status()
        attempt("ntfy", ntfy)

    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        def telegram():
            r = requests.post(f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/sendMessage",
                              json={"chat_id": env("TELEGRAM_CHAT_ID"),
                                    "text": f"{title}\n\n{body}\n{url}"}, timeout=15)
            r.raise_for_status()
        attempt("telegram", telegram)

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

    tw_sid, tw_tok = env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN")
    tw_from, tw_to = env("TWILIO_FROM"), env("TWILIO_TO")
    if tw_sid and tw_tok and tw_from and tw_to:
        api = f"https://api.twilio.com/2010-04-01/Accounts/{tw_sid}"
        attempt("sms", lambda: requests.post(
            f"{api}/Messages.json", auth=(tw_sid, tw_tok), timeout=15,
            data={"From": tw_from, "To": tw_to, "Body": f"{title}: {url}"}).raise_for_status())
        if urgent and env("TWILIO_CALL", "1") == "1":
            say = "Avengers Doomsday Infinity Vision tickets are on sale. " * 3
            attempt("call", lambda: requests.post(
                f"{api}/Calls.json", auth=(tw_sid, tw_tok), timeout=15,
                data={"From": tw_from, "To": tw_to,
                      "Twiml": f"<Response><Say>{say}</Say></Response>"}).raise_for_status())

    if env("SMTP_HOST") and env("EMAIL_TO"):
        def email():
            msg = EmailMessage()
            msg["Subject"], msg["From"], msg["To"] = title, env("SMTP_USER") or env("EMAIL_TO"), env("EMAIL_TO")
            msg.set_content(f"{body}\n\n{url}")
            with smtplib.SMTP(env("SMTP_HOST"), int(env("SMTP_PORT", "587")), timeout=30) as s:
                s.starttls()
                if env("SMTP_USER"):
                    s.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
                s.send_message(msg)
        attempt("email", email)

    if not sent:
        log("WARNING: no notification channel succeeded (is .env configured?)")
    else:
        log(f"notified via: {', '.join(sent)}")
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
    """One check cycle. Returns True once tickets are confirmed and alerts are done."""
    if state.get("done"):
        return True
    label = f"{MOVIE_KEYWORD} ({EXPERIENCE}) @ {CINEMA_NAME} on {TARGET_DATE:%a %d %b %Y}"
    pages = {"showtimes": SHOWTIMES_URL, "movie": MOVIE_URL}
    times: list[str] = []
    date_listed = False

    for name, url in pages.items():
        try:
            html = fetch(url)
        except Exception as e:
            log(f"fetch {name} failed: {e}")
            state["errors"] = state.get("errors", 0) + 1
            if state["errors"] == 20:
                notify("VOX monitor: fetch failing", f"20 failed fetches in a row. Last: {e}",
                       url, urgent=False)
            continue
        state["errors"] = 0
        if name == "showtimes":
            times = find_showtimes(html)
        else:
            date_listed = target_date_listed(html)

        fp = relevant_fingerprint(html)
        old = state.get("fp", {}).get(name)
        state.setdefault("fp", {})[name] = fp
        if old and old != fp and NOTIFY_ON_CHANGE and not times:
            notify("VOX page changed", f"The {name} page for {label} changed. Check it now.",
                   url, urgent=False)

    if date_listed and not state.get("date_alerted"):
        state["date_alerted"] = True
        notify(f"VOX: {TARGET_DATE:%d %b} now listed",
               f"{TARGET_DATE:%d %b} appears on the movie page. Tickets may be open: {label}",
               MOVIE_URL, urgent=True)

    if times:
        state["alerts"] = state.get("alerts", 0) + 1
        log(f"TICKETS FOUND: {times}")
        notify("DOOMSDAY TICKETS LIVE!",
               f"{label}\nShowtimes: {', '.join(times)}\nBook NOW!", BOOKING_URL, urgent=True)
        state["done"] = state["alerts"] >= ALERT_REPEATS
        return state["done"]
    log(f"not yet: {label}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run a single check and exit")
    ap.add_argument("--test-notify", action="store_true", help="send a test alert and exit")
    args = ap.parse_args()

    if args.test_notify:
        ok = notify("VOX monitor test", "If you see this, alerts work.", BOOKING_URL, urgent=False)
        return 0 if ok else 1

    log(f"watching {SHOWTIMES_URL} and {MOVIE_URL}")
    while True:
        state = load_state()
        done = check(state)
        save_state(state)
        if args.once or done:
            return 0
        time.sleep(INTERVAL + random.uniform(0, INTERVAL * 0.2))


if __name__ == "__main__":
    sys.exit(main())
