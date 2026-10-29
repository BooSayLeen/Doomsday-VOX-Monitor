import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { findShowtimes, fingerprint, targetDateListed } from "../src/parse.js";
import worker, { check } from "../src/index.js";

const LIVE = `
<div class="movies">
  <article class="movie"><h2 class="title">Avengers: Doomsday</h2>
    <div class="exp"><strong>Standard</strong><a href="/booking/1">13:00</a></div>
    <div class="exp"><strong>Infinity Vision</strong>
      <a href="/booking/2">19:30</a><a href="/booking/3">22:45</a></div>
  </article>
  <article class="movie"><h2 class="title">Other Film</h2>
    <div class="exp"><strong>Standard</strong><a href="/booking/4">15:00</a></div>
  </article>
</div>`;
const OTHER_MOVIE_HAS_EXP = `
<div class="movies">
  <article class="movie"><h2 class="title">Avengers: Doomsday</h2>
    <div class="exp"><strong>Standard</strong><a href="/booking/1">13:00</a></div>
  </article>
  <article class="movie"><h2 class="title">Other Film</h2>
    <div class="exp"><strong>Infinity Vision</strong><a href="/booking/4">15:00</a></div>
  </article>
</div>`;
const EMPTY = "<html><body><p>No showtimes available</p></body></html>";
const find = (h) => findShowtimes(h, "Doomsday", "Infinity Vision");

test("finds Infinity Vision times only", () => {
  assert.deepEqual(find(LIVE), ["19:30", "22:45"]);
});
test("ignores another movie's Infinity Vision", () => {
  assert.deepEqual(find(OTHER_MOVIE_HAS_EXP), []);
});
test("empty page", () => assert.deepEqual(find(EMPTY), []));
test("survives scripts, comments, void tags, entities", () => {
  const html = `<script>var x="<h2 class='title'>Doomsday</h2> Infinity Vision 10:00"</script>
    <!-- Doomsday Infinity Vision 11:00 --><img src=x><br/>` + LIVE.replace("Doomsday", "Doomsday&nbsp;");
  assert.deepEqual(find(html), ["19:30", "22:45"]);
});
test("date listed", () => {
  const d = new Date("2026-12-29T00:00:00Z");
  assert.ok(targetDateListed('<a href="?d=20261229">Tue 29 Dec</a>', d));
  assert.ok(!targetDateListed('<a href="?d=20261228">Mon 28 Dec</a>', d));
});
test("fingerprint ignores unrelated changes", async () => {
  const fp = (h) => fingerprint(h, ["Doomsday", "Infinity Vision"], "20261229");
  const pad = "x".repeat(1000);
  assert.equal(await fp(LIVE + pad + "<footer>a</footer>"), await fp(LIVE + pad + "<footer>b</footer>"));
  assert.notEqual(await fp(LIVE), await fp(OTHER_MOVIE_HAS_EXP));
});

test("CPU budget: big page parses fast", () => {
  const big = "<div>" + `<section class="ad"><p>filler text ${"lorem ".repeat(30)}</p></section>`.repeat(1500)
    + LIVE + "</div>";
  find(big);  // warm up
  const t0 = performance.now();
  const times = find(big);
  const ms = performance.now() - t0;
  console.log(`  ${(big.length / 1024).toFixed(0)} KB page parsed in ${ms.toFixed(1)} ms`);
  assert.deepEqual(times, ["19:30", "22:45"]);
});

// End-to-end: scheduled() with fake KV and a fake network.
function harness(pages) {
  const sent = [], kv = new Map();
  let writes = 0;
  globalThis.fetch = async (url, init = {}) => {
    url = String(url);
    if (url.includes("voxcinemas")) {
      const html = url.includes("showtimes") ? pages.showtimes : pages.movie;
      return new Response(html, { status: html === null ? 403 : 200 });
    }
    sent.push({ url, title: init.headers?.Title, priority: init.headers?.Priority });
    return new Response("ok");
  };
  const env = { NTFY_TOPIC: "t", ALERT_REPEATS: "2", ADMIN_KEY: "k",
    STATE: { get: async (k) => kv.get(k) ?? null, put: async (k, v) => { writes++; kv.set(k, v); },
             delete: async (k) => kv.delete(k) } };
  const run = async () => {
    const waits = [];
    await worker.scheduled({}, env, { waitUntil: (p) => waits.push(p) });
    await Promise.all(waits);
  };
  return { sent, env, run, writes: () => writes };
}

test("e2e: quiet, then tickets -> urgent alerts, then stops", async () => {
  const pages = { showtimes: EMPTY, movie: "<p>Dates: 27 Dec 28 Dec</p>" };
  const h = harness(pages);
  await h.run(); await h.run();
  assert.equal(h.sent.length, 0);
  assert.equal(h.writes(), 1);  // only the first run writes (fingerprints)
  pages.showtimes = LIVE;
  pages.movie = "<p>Dates: 28 Dec 29 Dec</p>";
  for (let i = 0; i < 4; i++) await h.run();
  const titles = h.sent.map((s) => s.title);
  assert.deepEqual(titles, ["DOOMSDAY TICKETS LIKELY LIVE", "VOX: 29 Dec now listed",
    "DOOMSDAY TICKETS LIVE!", "DOOMSDAY TICKETS LIVE!"]);
  assert.ok(h.sent.every((s) => s.priority === "5"));
});

test("e2e: blocked site -> one warning after 20 failures, writes stop", async () => {
  const h = harness({ showtimes: null, movie: null });
  for (let i = 0; i < 30; i++) await h.run();
  assert.deepEqual(h.sent.map((s) => s.title), ["VOX monitor: fetch failing"]);
  assert.equal(h.writes(), 20);
});

test("e2e: page change -> normal-priority heads-up", async () => {
  const pages = { showtimes: EMPTY, movie: "<h1>Avengers: Doomsday</h1><p>Coming soon</p>" };
  const h = harness(pages);
  await h.run();
  pages.movie = "<h1>Avengers: Doomsday</h1><p>Book now: 18 Dec</p>";
  await h.run();
  assert.deepEqual(h.sent.map((s) => [s.title, s.priority]), [["VOX page changed", "3"]]);
});

test("e2e: cheap alert is saved before the heavy parse runs", async () => {
  const h = harness({ showtimes: OTHER_MOVIE_HAS_EXP, movie: EMPTY });
  const kvPuts = [];
  const put = h.env.STATE.put;
  h.env.STATE.put = async (k, v) => { kvPuts.push(JSON.parse(v)); return put(k, v); };
  await h.run();
  assert.equal(kvPuts[0].likelyAlerted, true);
  assert.equal(kvPuts[0].fp, undefined);  // saved before fingerprinting/parsing
  await h.run();
  assert.deepEqual(h.sent.map((s) => s.title), ["DOOMSDAY TICKETS LIKELY LIVE"]);  // not repeated
});

test("http endpoints require the admin key", async () => {
  const h = harness({ showtimes: LIVE, movie: EMPTY });
  const call = (p) => worker.fetch(new Request(`https://w.dev${p}`), h.env);
  assert.equal((await call("/check")).status, 403);
  const res = await (await call("/check?key=k")).json();
  assert.deepEqual(res.times, ["19:30", "22:45"]);
  assert.equal(h.sent.length, 0);  // /check never alerts
  assert.deepEqual((await (await call("/test-notify?key=k")).json()).sent, ["ntfy"]);
});
