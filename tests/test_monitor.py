import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import monitor  # noqa: E402

LIVE = '<a href="/booking/0032-170001">19:30</a> <a href="/booking/0032-170002">22:45</a>'
OTHER_CINEMA = '<a href="/booking/0028-568458">19:30</a>'
EMPTY = "<p>No showtimes</p>"


def run(pages, state, n=1):
    sent = []
    monitor.fetch = lambda url: pages[url]
    monitor.notify = lambda title, body, url, urgent=True: sent.append((title, urgent)) or ["ntfy"]
    for _ in range(n):
        monitor.check(state)
    return sent


def test_urls():
    assert monitor.POLL_URL.endswith(
        "/showtimes?c=city-centre-alexandria&m=avengers-doomsday-infinity-vision&d=20261226")


def test_bookings_only_count_this_cinema():
    assert monitor.bookings(LIVE + OTHER_CINEMA) == ["/booking/0032-170001", "/booking/0032-170002"]
    assert monitor.bookings(OTHER_CINEMA) == []


def test_quiet():
    state = {}
    assert run({monitor.POLL_URL: EMPTY, monitor.EARLY_URL: EMPTY}, state, 3) == []


def test_live_alerts_repeat_then_stop():
    state = {}
    sent = run({monitor.POLL_URL: LIVE, monitor.EARLY_URL: LIVE}, state, 5)
    assert sent == [("DOOMSDAY INFINITY VISION TICKETS LIVE!", True)] * monitor.ALERT_REPEATS
    assert state["done"]


def test_early_warning_once():
    state = {}
    sent = run({monitor.POLL_URL: EMPTY, monitor.EARLY_URL: LIVE}, state, 3)
    assert len(sent) == 1 and "not Infinity Vision yet" in sent[0][0]


def test_errors_warn_once_at_10():
    state = {}
    def boom(url):
        raise TimeoutError("hang")
    sent = []
    monitor.fetch = boom
    monitor.notify = lambda title, body, url, urgent=True: sent.append(title) or []
    for _ in range(15):
        monitor.check(state)
    assert sent == ["VOX monitor: checks failing"]
