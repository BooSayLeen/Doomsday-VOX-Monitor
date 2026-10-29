"""
VOX Cinemas ticket monitor — Spider-Man: Brand New Day
City Centre Alexandria, 8 August 2026

Polls the VOX showtimes page every 5 minutes.
When one or more City Centre Alexandria showtimes appear for 8 Aug 2026,
plays a looping alarm and pops up a Windows notification.

Usage:
    py vox_monitor.py            # run in the foreground (Ctrl+C to stop)
    py vox_monitor.py --test     # fire the popup + alarm once, then exit
    py vox_monitor.py --stop     # stop a background instance

Or use the .bat launchers:
    run_background.bat           # start silently in the background
    stop.bat                     # stop the background instance
    test_notification.bat        # fire a test alert

Requires: Python 3.9+ on Windows, and one pip install:
    py -m pip install --user curl_cffi
"""

from __future__ import annotations

import argparse
import ctypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

# ---- Configuration ----------------------------------------------------------

TARGET_DATE       = "20260808"        # 8 August 2026
CINEMA_CODE       = "0032"            # City Centre Alexandria
MOVIE_SLUG        = "spider-man-brand-new-day"
POLL_SECONDS      = 300               # 5 minutes
HTTP_TIMEOUT      = 45
IMPERSONATE       = "chrome"          # curl_cffi TLS fingerprint

_HERE      = Path(__file__).resolve().parent
LOG_PATH   = _HERE / "vox_monitor.log"
PID_PATH   = _HERE / "vox_monitor.pid"

# Cinema-filtered showtimes URL — the /showtimes endpoint returns only the
# selected cinema's bookings, giving us a cleaner signal than the movie page
# (which mixes all cinemas together).
POLL_URL     = (
    f"https://egy.voxcinemas.com/showtimes"
    f"?c=city-centre-alexandria&m={MOVIE_SLUG}&d={TARGET_DATE}"
)
OPEN_ON_HIT  = (
    f"https://egy.voxcinemas.com/showtimes"
    f"?c=city-centre-alexandria&m={MOVIE_SLUG}"
)
BOOKING_LINK_RE = re.compile(rf"/booking/{CINEMA_CODE}-\d+")

# ---- Logging ----------------------------------------------------------------

def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass  # pythonw has no stdout
    try:
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass

# ---- HTTP -------------------------------------------------------------------

def _import_curl_cffi():
    try:
        from curl_cffi import requests as cffi_requests  # type: ignore
        return cffi_requests
    except ImportError:
        log("ERROR: curl_cffi not installed. Run:")
        log("    py -m pip install --user curl_cffi")
        sys.exit(1)

def fetch(url: str) -> str:
    """Fetch a URL bypassing VOX's TLS-fingerprint bot check."""
    r = _import_curl_cffi().get(url, impersonate=IMPERSONATE, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.text

def check_availability() -> list[str]:
    """Return sorted booking URLs for City Centre Alexandria on TARGET_DATE."""
    html = fetch(POLL_URL)
    return sorted(set(BOOKING_LINK_RE.findall(html)))

# ---- Alarm ------------------------------------------------------------------

class Alarm:
    """Loud looping alarm on a background thread (Windows only)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        try:
            import winsound
        except ImportError:
            log("winsound unavailable (are you on Windows?) — skipping audio")
            return
        # Two-tone urgent alarm, ~1s cycle, loops until stopped.
        while not self._stop.is_set():
            try:
                winsound.Beep(1200, 400)
                if self._stop.is_set():
                    break
                winsound.Beep(1800, 400)
            except RuntimeError:
                time.sleep(0.5)

# ---- Windows toast / popup --------------------------------------------------

# MessageBox flags: MB_OK | MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_TOPMOST
_MB_FLAGS = 0x00000000 | 0x00000040 | 0x00001000 | 0x00040000

def popup(title: str, message: str) -> None:
    """Blocking system-modal popup so the user *must* dismiss it."""
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, _MB_FLAGS)
    except Exception as exc:  # noqa: BLE001
        log(f"MessageBox failed: {exc!r}")

# ---- Notify -----------------------------------------------------------------

def notify(bookings: list[str], *, open_browser: bool = True,
           title_suffix: str = "") -> None:
    log(f"*** AVAILABLE! {len(bookings)} City Centre Alexandria showtime(s) "
        f"for {TARGET_DATE}")
    for b in bookings:
        log(f"    https://egy.voxcinemas.com{b}")

    if open_browser:
        try:
            webbrowser.open(OPEN_ON_HIT)
        except Exception as exc:  # noqa: BLE001
            log(f"Could not open browser: {exc!r}")

    alarm = Alarm()
    alarm.start()
    try:
        popup(
            f"VOX Cinemas — TICKETS AVAILABLE{title_suffix}",
            (
                f"Spider-Man: Brand New Day\n"
                f"City Centre Alexandria — 8 August 2026\n\n"
                f"{len(bookings)} showtime(s) now bookable.\n\n"
                f"The booking page has been opened in your browser.\n\n"
                f"Click OK to silence the alarm and keep monitoring."
            ),
        )
    finally:
        alarm.stop()
    log("Alarm dismissed. Resuming monitoring.")

# ---- PID handling -----------------------------------------------------------

def _write_pid() -> None:
    try:
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        log(f"Could not write PID file: {exc!r}")

def _clear_pid() -> None:
    try:
        PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass

def stop_background() -> int:
    if not PID_PATH.exists():
        print("No running VOX monitor found (no PID file).")
        return 1
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        print(f"Could not read PID file: {exc!r}")
        return 1
    print(f"Stopping VOX monitor (PID {pid})...")
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            check=False, capture_output=True, text=True,
        )
    except FileNotFoundError:
        print("taskkill not available on this system.")
        return 1
    _clear_pid()
    log(f"Background instance (PID {pid}) stopped by --stop")
    print("Stopped.")
    return 0

# ---- Modes ------------------------------------------------------------------

def run_test_mode() -> None:
    log("=" * 72)
    log("TEST MODE — firing a fake availability alert so you can see + hear it")
    log("=" * 72)
    fake = [
        "/booking/0032-999001",
        "/booking/0032-999002",
        "/booking/0032-999003",
    ]
    notify(fake, open_browser=False, title_suffix=" [TEST]")
    log("Test complete.")

def run_monitor() -> None:
    _write_pid()
    log("=" * 72)
    log("VOX monitor started")
    log(f"Movie   : Spider-Man: Brand New Day")
    log(f"Cinema  : City Centre Alexandria (code {CINEMA_CODE})")
    log(f"Date    : {TARGET_DATE[:4]}-{TARGET_DATE[4:6]}-{TARGET_DATE[6:]}")
    log(f"URL     : {POLL_URL}")
    log(f"Poll    : every {POLL_SECONDS // 60} minutes")
    log(f"PID     : {os.getpid()}   (stop with: py vox_monitor.py --stop)")
    log("=" * 72)

    consecutive_errors = 0
    try:
        while True:
            try:
                bookings = check_availability()
            except KeyboardInterrupt:
                log("Stopped by user.")
                return
            except Exception as exc:  # noqa: BLE001
                consecutive_errors += 1
                log(f"Fetch error ({consecutive_errors}): {exc!r}")
                backoff = min(POLL_SECONDS, 30 * consecutive_errors)
                log(f"Retrying in {backoff}s")
                try:
                    time.sleep(backoff)
                except KeyboardInterrupt:
                    log("Stopped by user.")
                    return
                continue

            consecutive_errors = 0
            if bookings:
                notify(bookings)
            else:
                log(f"Not yet available. Sleeping {POLL_SECONDS // 60} min...")

            try:
                time.sleep(POLL_SECONDS)
            except KeyboardInterrupt:
                log("Stopped by user.")
                return
    finally:
        _clear_pid()

# ---- Entry ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="VOX ticket monitor")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true",
                       help="fire the notification once and exit")
    group.add_argument("--stop", action="store_true",
                       help="stop the background monitor instance")
    args = parser.parse_args()

    if args.stop:
        return stop_background()
    if args.test:
        run_test_mode()
        return 0
    run_monitor()
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Stopped by user.")
        sys.exit(0)
