# Doomsday VOX Monitor

Watches VOX Cinemas Egypt for **Avengers: Doomsday — Infinity Vision** at
**City Centre Alexandria** on **29 Dec 2026**, and alerts your phone the moment
showtimes appear. Runs free on **Cloudflare Workers**, checking every minute (no card needed).

## How it detects tickets

Every minute it fetches:

1. the cinema's showtimes page for the target date (`/showtimes?c=city-centre-alexandria&d=20261229`)
2. the movie page (`/movies/avengers-doomsday`)

| Signal | Alert |
|---|---|
| The cinema's 29 Dec page mentions both Doomsday and Infinity Vision | **URGENT**, once |
| Doomsday + "Infinity Vision" + showtimes in the same block | **URGENT**, repeated `ALERT_REPEATS` times (one per minute), then it stops |
| 29 Dec appears on the movie page's date list | **URGENT**, once |
| Relevant page content changed at all | normal priority (safety net if VOX's layout differs from what the parser expects) |
| 20 checks fail in a row (blocked or site down) | normal priority, once |

## Deploy to Cloudflare (free, ~10 minutes)

You need a free [Cloudflare account](https://dash.cloudflare.com/sign-up) and Node.js 18+.

1. **Pick alert channels.** The easiest is ntfy: install the **ntfy** app, tap **+**, and
   subscribe to a long random topic name such as `doomsday-alerts-k38fj2`. On Android,
   allow ntfy's "Max priority" notifications to override Do Not Disturb.
2. **Fill in secrets:**
   ```bash
   cd worker
   cp secrets.example.json secrets.json   # set ADMIN_KEY, NTFY_TOPIC, and any other channels
   ```
3. **Deploy:**
   ```bash
   npm install
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret bulk secrets.json
   ```
   The first deploy creates the KV namespace that stores state. If your Wrangler
   version asks for an `id`, run `npx wrangler kv namespace create STATE` and add
   `id = "..."` under `[[kv_namespaces]]` in `wrangler.toml`.
4. **Test it.** Use the URL that `wrangler deploy` printed:
   - `https://doomsday-vox-monitor.<you>.workers.dev/test-notify?key=ADMIN_KEY` sends a test alert to your phone
   - `.../check?key=ADMIN_KEY` runs a live check without alerting. Look at `errors`:
     `HTTP 403 (blocked)` means VOX is blocking Cloudflare, and `HTTP 404` means the URL guess is wrong.
   - `.../?key=ADMIN_KEY` shows the saved state. `.../reset?key=ADMIN_KEY` re-arms the alerts.
5. **Watch the logs:** Cloudflare dashboard → Workers → `doomsday-vox-monitor` → Logs,
   or `npx wrangler tail`. You should see `not yet: ...` every minute.

To change what it watches (date, cinema, URLs), edit `[vars]` in `worker/wrangler.toml`
and run `npx wrangler deploy` again. If VOX's real page URLs differ, open the page
in your browser and copy its address into `SHOWTIMES_URL` / `MOVIE_URL`.

**Channels:** ntfy (with `NTFY_EMAIL` it forwards each alert to your email too),
Telegram, WhatsApp (CallMeBot), Pushover (emergency alerts repeat until you tap them),
Twilio SMS **and a phone call**, Discord. Configure as many as you like; they all fire.

**Free-plan limits:** cron every minute is free. State writes are skipped unless something
changed, which keeps KV under its 1,000 writes/day. Parsing is tuned for the 10 ms CPU
limit, and the urgent "likely live" alert is sent and saved *before* the heavy parse,
so a CPU-limit kill can't swallow it.

## Run locally instead (Python)

```bash
pip install -r requirements.txt
pip install playwright && playwright install chromium   # optional fallback if VOX blocks plain requests
cp .env.example .env    # fill in at least one channel
python monitor.py --test-notify
python monitor.py
```

## Tests

```bash
cd worker && npm test
pip install pytest && python -m pytest -q
```
