// Security fixes on main, bug still open.
//
// Somebody landed a fix for a security bug on main in the last few days, but the
// bug is still open. Almost everything downstream waits on the resolution: the
// uplift request, the per-version status flags and the advisory are all driven
// off a bug being closed as fixed. Until someone sets it, a fix that has shipped
// to Nightly looks, to every other row on the dashboard, like work that has not
// started.
//
// How it decides:
//
// 1. Walk every commit on main over the last WINDOW_DAYS and collect the bugs
//    they land, ignoring landings that were since backed out (commits.landed).
// 2. Ask Bugzilla which of those are in a security group and have no resolution.
//
// Both steps matter: the commit log knows a fix landed, and only Bugzilla knows
// whether anyone recorded it.
//
// The commit index always covers the full window, and both steps are cached for
// the whole window, so the range selector (1 to WINDOW_DAYS days) only
// re-filters what has already been fetched.

import * as bugzilla from "./bugzilla.js";
import { fetchCommits, landed as landedBugs } from "./commits.js";
import { SourceError, cache } from "./http.js";
import { Unavailable } from "./postfilters.js";
import { Chart, Eq, Query } from "./querydef.js";

const BRANCH = "main";
const WINDOW_DAYS = 14;
const DEFAULT_DAYS = 3;
const DAY_MS = 86_400_000;
// Two weeks of main is about 4,000 commits; allow headroom before calling it incomplete.
const MAX_PAGES = 80;
// The second step, as a Bugzilla query. The bug ids come from step 1.
export const QUERY = new Query([Eq("resolution", "---"), Chart("bug_group", "substring", "sec")]);
export const LANDED_COLUMN = "landed_on_main";

// sha -> commit, for the last WINDOW_DAYS of main. Refreshed incrementally:
// main's history is linear with committer dates in landing order, so only
// commits newer than the newest one already held need fetching.
const index = new Map();

const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

// Landed bugs over the window -> when they landed. Throws if GitHub fails:
// a stale index is missing exactly the landings this check exists to see.
async function refreshIndex() {
  const floor = Date.now() - WINDOW_DAYS * DAY_MS;
  const dates = [...index.values()].map((c) => Date.parse(c.date));
  // An hour of overlap absorbs any clock skew between landings; shas dedupe it.
  const since = dates.length ? Math.max(floor, Math.max(...dates) - 3_600_000) : floor;
  for (const c of await fetchCommits(BRANCH, iso(since), MAX_PAGES)) index.set(c.sha, c);
  for (const [sha, c] of index) if (Date.parse(c.date) < floor) index.delete(sha);
  return landedBugs([...index.values()]);
}

async function landedOnMain(fresh) {
  try {
    return await cache.getOrRun(["main-index"], refreshIndex, undefined, fresh);
  } catch (e) {
    if (e instanceof SourceError) throw new Unavailable(`commit history for ${BRANCH} unavailable: ${e.message}`);
    throw e;
  }
}

// Step 2 for every landed bug in the window, cached as one unit so moving
// the range selector does not go back to Bugzilla either.
function openSecBugs(ids, fields, key, fresh) {
  return cache.getOrRun(["sec-open-on-main", key, ids, fields], async () => {
    const out = [];
    for (let i = 0; i < ids.length; i += 300) {
      const params = [...QUERY.restParams(), ["id", ids.slice(i, i + 300).join(",")]];
      out.push(...await bugzilla.search(params, fields, key));
    }
    return out;
  }, undefined, fresh);
}

const plural = (d) => `${d} day${d > 1 ? "s" : ""}`;

function sourceLine(days, since = null, count = null) {
  const when = `in the last ${plural(days)}${since ? ` (since ${since})` : ""}`;
  const found = count !== null ? ` (${count} bugs), then` : ", then";
  return `bugs with a commit landed on ${BRANCH} ${when} and not backed out${found}`;
}

// Plugs into the runner in place of a single Bugzilla search.
export const SecOpenOnMain = {
  options: {
    name: "days",
    label: "Landed in the last",
    choices: Array.from({ length: WINDOW_DAYS }, (_, i) => [i + 1, plural(i + 1)]),
    default: DEFAULT_DAYS,
  },

  parse(opts) {
    const raw = String(opts.days ?? DEFAULT_DAYS).trim();
    const days = /^[+-]?\d+$/.test(raw) ? Number(raw) : DEFAULT_DAYS;
    return { days: Math.min(Math.max(days, 1), WINDOW_DAYS) };
  },

  lines(opts) {
    return [sourceLine(this.parse(opts).days), ...QUERY.lines()];
  },

  // [bugs, extra result fields] for the chosen range.
  async fetch(fields, key, opts, fresh = false) {
    const { days } = this.parse(opts);
    const landed = await landedOnMain(fresh);
    const cutoff = Date.now() - days * DAY_MS;
    const inRange = [...landed].filter(([, when]) => Date.parse(when) >= cutoff)
      .map(([b]) => b).sort((a, b) => a - b);
    const all = [...landed.keys()].sort((a, b) => a - b);
    const rows = await openSecBugs(all, fields.filter((f) => f !== LANDED_COLUMN), key, fresh);
    const keep = new Set(inRange);
    const bugs = rows.filter((b) => keep.has(b.id))
      .map((b) => ({ ...b, [LANDED_COLUMN]: landed.get(b.id).replace("T", " ").replace(/Z$/, "") }));
    return [bugs, {
      candidates: inRange.length,
      lines: [sourceLine(days, iso(Math.floor(cutoff / 1000) * 1000), inRange.length), ...QUERY.lines()],
    }];
  },
};
