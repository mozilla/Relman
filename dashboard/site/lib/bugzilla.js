// Bugzilla REST client. Every request runs as the reader's own API key, sent
// straight from this page to bugzilla.mozilla.org.

import { BUGZILLA_CONCURRENCY, BUGZILLA_ROW_CAP } from "./config.js";
import { SourceError, get } from "./http.js";

const REST = "https://bugzilla.mozilla.org/rest";

// What every table shows; checks add their own flag columns.
export const BASE_FIELDS = [
  "id", "summary", "status", "resolution", "product", "component",
  "assigned_to", "severity", "last_change_time", "groups",
];

// Bugzilla is shared and rate limited: cap how many requests this page has open.
// The newest waiter goes first, so after switching tabs the page in front of
// you is not stuck behind the one you left (whose work still finishes and
// fills the cache). The old page's requests are reordered, not cancelled; if
// they ever need stopping outright, pass an AbortSignal through instead.
let active = 0;
const waiting = [];
async function limited(fn) {
  while (active >= BUGZILLA_CONCURRENCY) await new Promise((resolve) => waiting.push(resolve));
  active++;
  try {
    return await fn();
  } finally {
    active--;
    waiting.pop()?.();
  }
}

async function call(path, params, key) {
  // Never follow a redirect: it would carry the API key to wherever it points.
  const r = await limited(() => get(`${REST}/${path}`, {
    params, headers: key ? { "X-Bugzilla-API-Key": key } : {}, redirect: "error",
  }));
  const text = await r.text();
  let body = null;
  try { body = JSON.parse(text); } catch { /* handled below */ }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    // An outage page or proxy error: show the start of it, as that is
    // usually what says what went wrong.
    const snippet = text.split(/\s+/).join(" ").trim().slice(0, 200);
    throw new SourceError(`Bugzilla answered HTTP ${r.status} with no JSON body${snippet ? `: ${snippet}` : ""}`);
  }
  if (body.error || r.status !== 200) {
    // Bugzilla's message is not always a string, and not always set.
    const m = body.message;
    const msg = (typeof m === "string" ? m : m != null ? JSON.stringify(m) : "").trim();
    const code = body.code ?? null;
    throw new SourceError(`Bugzilla error${code ? ` ${code}` : ""}: ${msg || `HTTP ${r.status}`}`, code);
  }
  return body;
}

// Run a search. `params` already carries the order.
export async function search(params, fields, key) {
  const p = [...params, ["include_fields", fields.join(",")], ["limit", String(BUGZILLA_ROW_CAP)]];
  return (await call("bug", p, key)).bugs;
}

// How many bugs match, without the row cap.
export async function count(params, key) {
  return (await call("bug", [...params, ["count_only", "1"]], key)).bug_count;
}

export async function byIds(ids, fields, key) {
  const out = [];
  for (let i = 0; i < ids.length; i += 300) {
    const p = [["id", ids.slice(i, i + 300).join(",")], ["include_fields", fields.join(",")]];
    out.push(...(await call("bug", p, key)).bugs);
  }
  return out;
}

// Attachments (with flags) per bug id.
export async function attachments(ids, key) {
  const out = new Map();
  for (let i = 0; i < ids.length; i += 100) {
    const chunk = ids.slice(i, i + 100);
    const p = [...chunk.slice(1).map((b) => ["ids", String(b)]), ["include_fields", "id,bug_id,is_obsolete,flags"]];
    const body = await call(`bug/${chunk[0]}/attachment`, p, key);
    for (const [bugId, atts] of Object.entries(body.bugs ?? {})) out.set(Number(bugId), atts);
  }
  return out;
}

export function whoami(key) {
  return call("whoami", [], key);
}
