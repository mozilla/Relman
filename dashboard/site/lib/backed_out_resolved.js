// Backed out of main, never relanded, bug resolved as fixed (Nightly).
//
// A landing was backed out, and the bug was resolved as fixed anyway: usually
// it was resolved by accident after the backout, or the backout came after
// the merge and nobody reopened it. Every downstream check reads the bug as
// fixed on Nightly when the fix is not there.
//
// How it decides:
//
// 1. Walk every commit on main over the last WINDOW_DAYS and collect the bugs
//    a backout names, leaving out any with a landing still live: relanded
//    after the backout, or another part of the bug that was never backed out.
//    Only landings in the window are seen, so a part that landed before it and
//    is still in does not count: the bug shows up when it should not. Reading
//    further back costs GitHub pages per load; bin/backed-out.js reads the
//    bug's whole history from a local checkout instead.
// 2. Ask Bugzilla which of those are resolved as fixed.
//
// The commit index always covers the full window, and both steps are cached for
// the whole window, so the range selector (1 to WINDOW_DAYS days) only
// re-filters what has already been fetched.

import * as bugzilla from "./bugzilla.js";
import { backedOut, fetchCommits, landed } from "./commits.js";
import { SourceError, cache } from "./http.js";
import { Unavailable } from "./postfilters.js";
import { Eq, Query } from "./querydef.js";

const BRANCH = "main";
const WINDOW_DAYS = 14;
const DEFAULT_DAYS = 3;
const DAY_MS = 86_400_000;
// Two weeks of main is about 4,000 commits; allow headroom before calling it incomplete.
const MAX_PAGES = 80;
// The second step, as a Bugzilla query. The bug ids come from step 1.
export const QUERY = new Query([Eq("resolution", "FIXED")]);
export const BACKED_OUT_COLUMN = "backed_out_of_main";

// sha -> commit, for the last WINDOW_DAYS of main. Refreshed incrementally:
// main's history is linear with committer dates in landing order, so only
// commits newer than the newest one already held need fetching.
const index = new Map();

const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

// Backed-out bugs with no live landing -> when the latest backout was.
// Throws if GitHub fails: a stale index is missing exactly the backouts and
// relandings this check reads.
async function refreshIndex() {
  const floor = Date.now() - WINDOW_DAYS * DAY_MS;
  const dates = [...index.values()].map((c) => Date.parse(c.date));
  // An hour of overlap absorbs any clock skew between landings; shas dedupe it.
  const since = dates.length ? Math.max(floor, Math.max(...dates) - 3_600_000) : floor;
  for (const c of await fetchCommits(BRANCH, iso(since), MAX_PAGES)) index.set(c.sha, c);
  for (const [sha, c] of index) if (Date.parse(c.date) < floor) index.delete(sha);
  const commits = [...index.values()];
  const live = landed(commits);
  return new Map([...backedOut(commits)].filter(([b]) => !live.has(b)));
}

async function backedOutOfMain(fresh) {
  try {
    return await cache.getOrRun(["backed-out-index"], refreshIndex, undefined, fresh);
  } catch (e) {
    if (e instanceof SourceError) throw new Unavailable(`commit history for ${BRANCH} unavailable: ${e.message}`);
    throw e;
  }
}

// Step 2 for every backed-out bug in the window, cached as one unit so moving
// the range selector does not go back to Bugzilla either.
function resolvedBugs(ids, fields, key, fresh) {
  return cache.getOrRun(["backed-out-resolved", key, ids, fields], async () => {
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
  return `bugs backed out of ${BRANCH} ${when} and not relanded${found}`;
}

// Plugs into the runner in place of a single Bugzilla search.
export const BackedOutResolved = {
  options: {
    name: "days",
    label: "Backed out in the last",
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
    const out = await backedOutOfMain(fresh);
    const cutoff = Date.now() - days * DAY_MS;
    const inRange = new Set([...out].filter(([, when]) => Date.parse(when) >= cutoff).map(([b]) => b));
    const all = [...out.keys()].sort((a, b) => a - b);
    const rows = all.length ? await resolvedBugs(all, fields.filter((f) => f !== BACKED_OUT_COLUMN), key, fresh) : [];
    const bugs = rows.filter((b) => inRange.has(b.id))
      .map((b) => ({ ...b, [BACKED_OUT_COLUMN]: out.get(b.id).replace("T", " ").replace(/Z$/, "") }));
    return [bugs, {
      candidates: inRange.size,
      lines: [sourceLine(days, iso(Math.floor(cutoff / 1000) * 1000), inRange.size), ...QUERY.lines()],
    }];
  },
};
