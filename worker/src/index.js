// Cloudflare Worker: checks VOX Cinemas Egypt every minute (cron) and alerts
// the phone once Avengers: Doomsday Infinity Vision showtimes for the target
// date appear at City Centre Alexandria.

import { findShowtimes, fingerprint, mentionsBoth, targetDateListed } from "./parse.js";
import { notify } from "./notify.js";

const HEADERS = {
  "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.9",
};

export function config(env) {
  const base = env.VOX_BASE_URL || "https://egy.voxcinemas.com";
  const cinemaSlug = env.CINEMA_SLUG || "city-centre-alexandria";
  const date = new Date(`${env.TARGET_DATE || "2026-12-29"}T00:00:00Z`);
  const dateCompact = date.toISOString().slice(0, 10).replaceAll("-", "");
  const cfg = {
    cinemaName: env.CINEMA_NAME || "City Centre Alexandria",
    movie: env.MOVIE_KEYWORD || "Doomsday",
    experience: env.EXPERIENCE || "Infinity Vision",
    date, dateCompact,
    showtimesUrl: env.SHOWTIMES_URL || `${base}/showtimes?c=${cinemaSlug}&d=${dateCompact}`,
    movieUrl: env.MOVIE_URL || `${base}/movies/${env.MOVIE_SLUG || "avengers-doomsday"}`,
    notifyOnChange: (env.NOTIFY_ON_CHANGE ?? "1") === "1",
    alertRepeats: Number(env.ALERT_REPEATS || 3),
  };
  const day = date.toLocaleDateString("en-GB",
    { weekday: "short", day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
  cfg.label = `${cfg.movie} (${cfg.experience}) @ ${cfg.cinemaName} on ${day}`;
  return cfg;
}

function looksBlocked(status, html) {
  if ([403, 429, 503].includes(status)) return true;
  const head = html.slice(0, 5000).toLowerCase();
  return ["just a moment", "cf-challenge", "access denied", "captcha", "request unsuccessful"]
    .some((s) => head.includes(s));
}

async function fetchPage(url) {
  const res = await fetch(url, { headers: HEADERS, redirect: "follow" });
  const html = await res.text();
  if (res.status >= 400 || looksBlocked(res.status, html)) {
    throw new Error(`HTTP ${res.status}${looksBlocked(res.status, html) ? " (blocked)" : ""} for ${url}`);
  }
  return html;
}

/** One check. Mutates `state`; returns a summary for logs and the /check endpoint.
 *  `save` persists state early, before the CPU-heavy parse, so a run killed by the
 *  free plan's CPU limit can't swallow (or endlessly repeat) the first alert. */
export async function check(env, state, { dryRun = false, save = async () => {} } = {}) {
  const cfg = config(env);
  const alert = (msg) => (dryRun ? Promise.resolve([]) : notify(env, msg));
  const summary = { label: cfg.label, times: [], dateListed: false, errors: [] };
  if (state.done) return { ...summary, done: true };

  const pages = { showtimes: cfg.showtimesUrl, movie: cfg.movieUrl };
  const htmls = await Promise.all(Object.entries(pages).map(async ([name, url]) => {
    try {
      return [name, await fetchPage(url)];
    } catch (e) {
      summary.errors.push(`${name}: ${e.message}`);
      return [name, null];
    }
  }));

  const showtimesHtml = Object.fromEntries(htmls).showtimes;
  if (showtimesHtml && !state.likelyAlerted && mentionsBoth(showtimesHtml, cfg.movie, cfg.experience)) {
    state.likelyAlerted = true;
    await alert({ title: "DOOMSDAY TICKETS LIKELY LIVE", url: cfg.showtimesUrl,
      body: `${cfg.label}: the cinema's page for that date now lists ${cfg.movie} and ` +
        `${cfg.experience}. Open it now!` });
    if (!dryRun) await save(state);
  }

  const changed = [];
  for (const [name, html] of htmls) {
    if (html === null) continue;
    if (name === "showtimes") summary.times = findShowtimes(html, cfg.movie, cfg.experience);
    else summary.dateListed = targetDateListed(html, cfg.date);
    const fp = await fingerprint(html, [cfg.movie, cfg.experience, cfg.cinemaName], cfg.dateCompact);
    state.fp ??= {};
    if (state.fp[name] && state.fp[name] !== fp) changed.push(name);
    state.fp[name] = fp;
  }

  if (summary.errors.length) {
    console.log(`fetch failed: ${summary.errors.join("; ")}`);
    // Stop counting at 20 so a long outage doesn't burn the KV write quota.
    if ((state.errors || 0) < 20) {
      state.errors = (state.errors || 0) + 1;
      if (state.errors === 20) {
        await alert({ title: "VOX monitor: fetch failing", urgent: false, url: cfg.showtimesUrl,
          body: `20 failed checks in a row. Last: ${summary.errors.join("; ")}` });
      }
    }
  } else if (state.errors) {
    state.errors = 0;
  }

  if (changed.length && cfg.notifyOnChange && !summary.times.length) {
    await alert({ title: "VOX page changed", urgent: false, url: pages[changed[0]],
      body: `The ${changed.join(" + ")} page for ${cfg.label} changed. Check it now.` });
  }

  if (summary.dateListed && !state.dateAlerted) {
    state.dateAlerted = true;
    const dm = cfg.date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" });
    await alert({ title: `VOX: ${dm} now listed`, url: cfg.movieUrl,
      body: `${dm} appears on the movie page. Tickets may be open: ${cfg.label}` });
  }

  if (summary.times.length) {
    state.alerts = (state.alerts || 0) + 1;
    console.log(`TICKETS FOUND: ${summary.times.join(", ")}`);
    await alert({ title: "DOOMSDAY TICKETS LIVE!", url: cfg.showtimesUrl,
      body: `${cfg.label}\nShowtimes: ${summary.times.join(", ")}\nBook NOW!` });
    state.done = state.alerts >= cfg.alertRepeats;
  } else {
    console.log(`not yet: ${cfg.label}`);
  }
  return summary;
}

async function runScheduled(env) {
  let raw = await env.STATE.get("state");
  const state = raw ? JSON.parse(raw) : {};
  const save = async (s) => {
    const next = JSON.stringify(s);
    if (next !== raw) await env.STATE.put("state", next);  // KV free tier: 1,000 writes/day
    raw = next;
  };
  await check(env, state, { save });
  await save(state);
}

export default {
  async scheduled(event, env) {
    await runScheduled(env);  // awaited (not waitUntil) so runs can't overlap and race on state
  },

  async fetch(req, env) {
    const url = new URL(req.url);
    if (!env.ADMIN_KEY || url.searchParams.get("key") !== env.ADMIN_KEY) {
      return new Response("forbidden", { status: 403 });
    }
    const json = (o) => new Response(JSON.stringify(o, null, 2),
      { headers: { "Content-Type": "application/json" } });

    if (url.pathname === "/test-notify") {
      const sent = await notify(env, { title: "VOX monitor test", urgent: false,
        body: "If you see this, alerts work.", url: config(env).showtimesUrl });
      return json({ sent });
    }
    if (url.pathname === "/check") {  // live check without alerting or saving state
      return json(await check(env, {}, { dryRun: true }));
    }
    if (url.pathname === "/reset") {
      await env.STATE.delete("state");
      return json({ reset: true });
    }
    return json({ state: JSON.parse((await env.STATE.get("state")) || "{}"),
      endpoints: ["/check", "/test-notify", "/reset"] });
  },
};
