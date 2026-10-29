// Sends an alert on every channel configured in the Worker's secrets/vars.

async function ok(res) {
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
}

const form = (data) => new URLSearchParams(data);

/** Returns the names of the channels that succeeded. */
export async function notify(env, { title, body, url, urgent = true }) {
  const jobs = {};

  if (env.NTFY_TOPIC) {
    const headers = {
      Title: title, Priority: urgent ? "5" : "3", Tags: "rotating_light,movie_camera",
      Click: url, Actions: `view, Book now, ${url}`,
    };
    if (env.NTFY_EMAIL) headers.Email = env.NTFY_EMAIL;
    jobs.ntfy = () => fetch(`${env.NTFY_SERVER || "https://ntfy.sh"}/${env.NTFY_TOPIC}`,
      { method: "POST", body, headers });
  }

  if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
    jobs.telegram = () => fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text: `${title}\n\n${body}\n${url}` }),
    });
  }

  if (env.PUSHOVER_TOKEN && env.PUSHOVER_USER) {
    const data = { token: env.PUSHOVER_TOKEN, user: env.PUSHOVER_USER, title, message: body,
      url, url_title: "Book now", priority: urgent ? "2" : "0", sound: "siren" };
    if (urgent) Object.assign(data, { retry: "60", expire: "3600" });  // repeats until acknowledged
    jobs.pushover = () => fetch("https://api.pushover.net/1/messages.json",
      { method: "POST", body: form(data) });
  }

  if (env.DISCORD_WEBHOOK_URL) {
    jobs.discord = () => fetch(env.DISCORD_WEBHOOK_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: `@everyone **${title}**\n${body}\n${url}` }),
    });
  }

  if (env.CALLMEBOT_PHONE && env.CALLMEBOT_APIKEY) {  // free WhatsApp messages
    const q = form({ phone: env.CALLMEBOT_PHONE, apikey: env.CALLMEBOT_APIKEY,
      text: `${title}\n${body}\n${url}` });
    jobs.whatsapp = () => fetch(`https://api.callmebot.com/whatsapp.php?${q}`);
  }

  const { TWILIO_ACCOUNT_SID: sid, TWILIO_AUTH_TOKEN: tok, TWILIO_FROM: from, TWILIO_TO: to } = env;
  if (sid && tok && from && to) {
    const api = `https://api.twilio.com/2010-04-01/Accounts/${sid}`;
    const headers = { Authorization: `Basic ${btoa(`${sid}:${tok}`)}` };
    jobs.sms = () => fetch(`${api}/Messages.json`,
      { method: "POST", headers, body: form({ From: from, To: to, Body: `${title}: ${url}` }) });
    if (urgent && env.TWILIO_CALL !== "0") {
      const say = "Avengers Doomsday Infinity Vision tickets are on sale. ".repeat(3);
      jobs.call = () => fetch(`${api}/Calls.json`, { method: "POST", headers,
        body: form({ From: from, To: to, Twiml: `<Response><Say>${say}</Say></Response>` }) });
    }
  }

  const names = Object.keys(jobs);
  const results = await Promise.allSettled(names.map((n) => jobs[n]().then(ok)));
  const sent = [];
  results.forEach((r, i) => {
    if (r.status === "fulfilled") sent.push(names[i]);
    else console.log(`notify ${names[i]} failed: ${r.reason}`);
  });
  console.log(sent.length ? `notified via: ${sent.join(", ")}` : "WARNING: no channel succeeded");
  return sent;
}
