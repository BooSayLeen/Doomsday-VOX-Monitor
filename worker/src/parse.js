// HTML analysis for the VOX pages, kept cheap enough for the Workers free plan
// (10 ms CPU per run). Same logic as monitor.py, with a minimal tag-tree parser
// instead of BeautifulSoup.

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "param", "source", "track", "wbr"]);
const TIME_RE = /\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*[AaPp][Mm])?\b/g;
const HAS_TIME = new RegExp(TIME_RE.source);  // non-global: .test() must not keep state
const HEADINGS = new Set(["h1", "h2", "h3", "h4", "h5"]);

export const norm = (s) => s.replace(/\s+/g, " ").trim().toLowerCase();

function decode(s) {
  return s.replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&#0?39;|&apos;/g, "'")
    .replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">");
}

function stripNoise(html) {
  return html.replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<(script|style|noscript|template)\b[\s\S]*?<\/\1\s*>/gi, "");
}

export function parseTree(html) {
  const root = { tag: "#root", cls: "", href: null, children: [], parent: null };
  let cur = root;
  const re = /<(\/?)([a-zA-Z][\w-]*)([^>]*)>|([^<]+)/g;
  const src = stripNoise(html);
  let m;
  while ((m = re.exec(src))) {
    if (m[4] !== undefined) {
      const t = decode(m[4]);
      if (t.trim()) cur.children.push(t);
      continue;
    }
    const tag = m[2].toLowerCase();
    if (m[1]) {  // closing tag: pop to the nearest matching open element
      for (let n = cur; n && n !== root; n = n.parent) {
        if (n.tag === tag) { cur = n.parent; break; }
      }
      continue;
    }
    const attrs = m[3];
    const node = {
      tag,
      cls: norm((attrs.match(/\bclass\s*=\s*["']([^"']*)["']/i) || [])[1] || ""),
      href: (attrs.match(/\bhref\s*=\s*["']([^"']*)["']/i) || [])[1] ?? null,
      children: [],
      parent: cur,
    };
    cur.children.push(node);
    if (!VOID.has(tag) && !attrs.trimEnd().endsWith("/")) cur = node;
  }
  return root;
}

const textCache = new WeakMap();
export function textOf(node) {
  if (typeof node === "string") return node;
  let t = textCache.get(node);
  if (t === undefined) {
    t = node.children.map(textOf).join(" ");
    textCache.set(node, t);
  }
  return t;
}

function* walk(node) {
  for (const c of node.children) {
    if (typeof c !== "string") {
      yield c;
      yield* walk(c);
    }
  }
}

const ownText = (n) => n.children.filter((c) => typeof c === "string").join(" ");

// Case-insensitive, whitespace-tolerant keyword test without normalising every string.
const matcher = (keyword) => new RegExp(
  norm(keyword).split(" ").map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s+"), "i");

/** Cheap (sub-millisecond) test: does the page mention both keywords at all? */
export const mentionsBoth = (html, movieKeyword, experience) =>
  matcher(movieKeyword).test(html) && matcher(experience).test(html);

/** Showtimes for `movie` + `exp` in the page, or [] if none. */
export function findShowtimes(html, movieKeyword, experience) {
  const hasMovie = matcher(movieKeyword), hasExp = matcher(experience);
  if (!hasMovie.test(html) || !hasExp.test(html)) return [];  // cheap fast path

  const root = parseTree(html);
  const found = new Set();
  for (const title of walk(root)) {
    if (!hasMovie.test(ownText(title))) continue;
    let block = title;
    for (let i = 0; i < 12; i++) {
      if (!block.parent || hasExp.test(textOf(block))) break;
      block = block.parent;
    }
    if (!hasExp.test(textOf(block))) continue;
    // Elements shaped like our title but naming other movies mean we climbed
    // into a multi-movie list, so the experience we found isn't ours.
    if (title.cls || HEADINGS.has(title.tag)) {
      const clash = [...walk(block)].some((n) => n.tag === title.tag && n.cls === title.cls &&
        !hasMovie.test(textOf(n)));
      if (clash) continue;
    }
    for (const el of walk(block)) {
      if (!hasExp.test(ownText(el))) continue;
      let sub = el;
      for (let i = 0; i < 4; i++) {
        if (!sub.parent || sub.parent === block) break;
        if (HAS_TIME.test(textOf(sub)) || [...walk(sub)].some((n) => n.tag === "a" && n.href)) break;
        sub = sub.parent;
      }
      for (const t of textOf(sub).match(TIME_RE) || []) found.add(t.trim());
      for (const a of walk(sub)) {
        if (a.tag !== "a" || !a.href) continue;
        const label = textOf(a).trim();
        if (a.href.toLowerCase().includes("book") || HAS_TIME.test(label)) found.add(label || a.href);
      }
    }
  }
  return [...found].sort();
}

/** Hash of the page regions that matter, so layout/ads changes don't trigger alerts. */
export async function fingerprint(html, keywords, dateCompact) {
  const clean = stripNoise(html);
  const low = clean.toLowerCase();
  const snippets = new Set();
  for (const k of keywords.map(norm)) {
    for (let i = low.indexOf(k); i !== -1; i = low.indexOf(k, i + k.length)) {
      const seg = clean.slice(Math.max(0, i - 300), i + 300).replace(/<[^>]*>?/g, " ");
      snippets.add(norm(decode(seg)).replace(/^\S*\s|\s\S*$/g, ""));  // drop cut words
    }
  }
  const hrefs = new Set();
  for (const m of clean.matchAll(/href\s*=\s*["']([^"']+)["']/gi)) {
    if (m[1].includes(dateCompact) || m[1].toLowerCase().includes("book")) hrefs.add(m[1]);
  }
  const data = new TextEncoder().encode(JSON.stringify([[...snippets].sort(), [...hrefs].sort()]));
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function targetDateListed(html, date) {
  const mon = date.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const month = date.toLocaleString("en-US", { month: "long", timeZone: "UTC" });
  const d = date.getUTCDate();
  const iso = date.toISOString().slice(0, 10);
  const pats = [iso.replaceAll("-", ""), iso, `${d} ${mon}`, `${mon} ${d}`, `${d} ${month}`];
  const low = html.toLowerCase();
  return pats.some((p) => low.includes(p.toLowerCase()));
}
