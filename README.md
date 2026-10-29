# Doomsday VOX Monitor

Watches VOX Cinemas Egypt for **Avengers: Doomsday (Infinity Vision)** at
**City Centre Alexandria** on **26 Dec 2026**, and alerts your phone when tickets drop.
It runs in the cloud on GitHub Actions, so your laptop can be off.

## How it works

Every 5 minutes GitHub runs `monitor.py`, which fetches:

```
https://egy.voxcinemas.com/showtimes?c=city-centre-alexandria&m=avengers-doomsday-infinity-vision&d=20261226
```

VOX lists Infinity Vision as its own movie (`avengers-doomsday-infinity-vision`).
That page has `/booking/0032-…` links (0032 = City Centre Alexandria) only once
that date is on sale. VOX hangs on non-browser requests, so pages are fetched with
`curl_cffi` impersonating Chrome, the same trick as the old Spider-Man script.

| Signal | Alert |
|---|---|
| Infinity Vision booking links for 26 Dec appear | **URGENT** on every channel, repeated `ALERT_REPEATS` (3) runs, then stops |
| Regular Doomsday becomes bookable for 26 Dec | **URGENT**, once (early warning) |
| 10 checks fail in a row | normal priority, once |

## Setup

See the step-by-step guide in the conversation, or in short:

1. Make the repo **public** (Settings → General → Danger Zone). Private repos get only
   2,000 free Actions minutes a month, which runs out in about a week at this pace.
   Secrets stay private either way.
2. Install the **ntfy** app and subscribe to a long random topic name.
3. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret**
   and add `NTFY_TOPIC` (plus any other channels, listed in `.env.example`).
4. Go to **Actions → VOX ticket monitor → Run workflow**, tick *Send a test alert*,
   and run it. Your phone should buzz.
5. Done. It runs every 5 minutes by itself.

To watch a different date, add a repository **variable** (not a secret) `TARGET_DATE`, e.g. `2026-12-27`.
To re-arm after it has alerted, delete the `vox-state-…` caches under **Actions → Caches**.

## Backup trigger (cron-job.org)

GitHub's own schedule can start late or skip runs, so a free external timer also
starts the workflow every 2 minutes through the GitHub API:

- **Token:** a fine-grained personal access token limited to this repo, with
  *Actions: Read and write* permission.
- **cron-job.org job:** `POST https://api.github.com/repos/BooSayLeen/Doomsday-VOX-Monitor/actions/workflows/monitor.yml/dispatches`
  with headers `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
  and body `{"ref":"claude/fervent-darwin-5pv8zh"}`. GitHub answers `204` on success.

If both triggers fire together, the workflow's concurrency group runs them one after the other.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env    # fill in at least one channel
python monitor.py --test-notify
python monitor.py
```

## Tests

```bash
pip install pytest && python -m pytest -q
```
