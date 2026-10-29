import os
import sys
from pathlib import Path

os.environ.setdefault("TARGET_DATE", "2026-12-29")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import monitor  # noqa: E402

LIVE = """
<div class="movies">
  <article class="movie"><h2 class="title">Avengers: Doomsday</h2>
    <div class="exp"><strong>Standard</strong><a href="/booking/1">13:00</a></div>
    <div class="exp"><strong>Infinity Vision</strong>
      <a href="/booking/2">19:30</a><a href="/booking/3">22:45</a></div>
  </article>
  <article class="movie"><h2 class="title">Other Film</h2>
    <div class="exp"><strong>Standard</strong><a href="/booking/4">15:00</a></div>
  </article>
</div>"""

NOT_LIVE_OTHER_MOVIE_HAS_EXP = """
<div class="movies">
  <article class="movie"><h2 class="title">Avengers: Doomsday</h2>
    <div class="exp"><strong>Standard</strong><a href="/booking/1">13:00</a></div>
  </article>
  <article class="movie"><h2 class="title">Other Film</h2>
    <div class="exp"><strong>Infinity Vision</strong><a href="/booking/4">15:00</a></div>
  </article>
</div>"""

EMPTY = "<html><body><p>No showtimes available</p></body></html>"


def test_live():
    times = monitor.find_showtimes(LIVE)
    assert "19:30" in times and "22:45" in times
    assert "13:00" not in times  # Standard showing must not count


def test_other_movie_experience_is_ignored():
    assert monitor.find_showtimes(NOT_LIVE_OTHER_MOVIE_HAS_EXP) == []


def test_empty():
    assert monitor.find_showtimes(EMPTY) == []


def test_date_listed():
    assert monitor.target_date_listed('<a href="?d=20261229">Tue 29 Dec</a>')
    assert not monitor.target_date_listed('<a href="?d=20261228">Mon 28 Dec</a>')


def test_fingerprint_ignores_noise():
    a = monitor.relevant_fingerprint(LIVE + "<footer>x</footer>")
    b = monitor.relevant_fingerprint(LIVE + "<footer>y</footer>")
    assert a == b != monitor.relevant_fingerprint(EMPTY)
