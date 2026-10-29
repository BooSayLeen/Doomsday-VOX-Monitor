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

## Deploy to Firebase (runs in the cloud every minute)

Needs the **Blaze** plan (a card on file). This usage stays inside the free tier:
about 44k function runs a month against 2M free, and 1 Cloud Scheduler job against 3 free.

1. In the [Firebase console](https://console.firebase.google.com): create a project,
   upgrade it to Blaze, then **Build → Firestore Database → Create database**.
2. Configure alerts:
   ```bash
   cp functions/.env.example functions/.env    # fill in ADMIN_KEY + at least one channel
   ```
3. Deploy (needs Node.js and Python 3.12):
   ```bash
   npm install -g firebase-tools
   firebase login
   python3.12 -m venv functions/venv
   functions/venv/bin/pip install -r functions/requirements.txt
   firebase deploy --only functions --project YOUR_PROJECT_ID
   ```
4. Test your phone alerts. Open the `test_notify` URL printed by the deploy:
   `https://europe-west1-YOUR_PROJECT_ID.cloudfunctions.net/test_notify?key=YOUR_ADMIN_KEY`
5. Watch it run: Firebase console → Functions → `vox_monitor` → Logs. You should see
   `not yet: ...` every minute. Repeated `fetch ... failed` lines mean VOX is
   blocking Google's servers. You'll also get an alert after 20 failures in a row.

To change settings later, edit `functions/.env` and run `firebase deploy` again.
Once tickets are found and the alerts have been sent, it stops alerting. To re-arm it,
delete the `vox_monitor/state` document in Firestore.

## Run locally instead

```bash
pip install -r requirements.txt
pip install playwright && playwright install chromium   # optional fallback
cp functions/.env.example functions/.env
python functions/monitor.py --test-notify
python functions/monitor.py
```

## Tests

```bash
pip install pytest && python -m pytest -q
```
