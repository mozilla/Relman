// Run checks and shape their results.
//
// A result's `state` is one of:
//   ok           count is real (it may be zero)
//   needs_key    the check is meaningless without an API key, so it did not run
//   needs_phab   the check reads Phabricator and there is no Phabricator token
//   unavailable  a cross-check source failed; there is deliberately no count
//   error        the query itself failed

import * as bugzilla from "./bugzilla.js";
import { BUGZILLA_ROW_CAP } from "./config.js";
import { SourceError, cache } from "./http.js";
import { Unavailable } from "./postfilters.js";
import { idsUrl } from "./querydef.js";

const IDS_URL_MAX = 400; // past this many ids the bug_id link gets too long to open (BugDash's limit too)

// A link to exactly these bugs, or null when there are none or too many.
export const idsLink = (bugs) => (bugs.length > 0 && bugs.length <= IDS_URL_MAX ? idsUrl(bugs.map((b) => b.id)) : null);

const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

// Sec ratings, most severe first; the sec rating column sorts in this order.
const SEC_RATINGS = ["sec-critical", "sec-high", "sec-moderate", "sec-low", "sec-want", "sec-other"];

// Columns worked out from another Bugzilla field: name -> { field, value(bug),
// sortKey(shown) }. sortKey is optional and gets the cell's text; without it
// the table sorts on the text itself. Any other column is a Bugzilla field shown as it is.
export const DERIVED = {
  sec_rating: {
    field: "keywords",
    value: (bug) => (bug.keywords ?? []).filter((k) => k.startsWith("sec-")),
    sortKey: (shown) => { // unrated, and keywords not in the list, come last
      const i = SEC_RATINGS.findIndex((r) => shown.split(", ").includes(r));
      return i < 0 ? SEC_RATINGS.length : i;
    },
  },
};
const fieldsFor = ({ columns, filters }) =>
  [...new Set([...columns.map((c) => DERIVED[c]?.field ?? c), ...filters.flatMap((f) => f.fields)])];

function shape(bug, { columns, filters }) {
  const who = bug.assigned_to_detail ?? {}; // Bugzilla sends it with assigned_to
  return {
    id: bug.id ?? null, // null for a row about a revision with no bug
    summary: bug.summary ?? "",
    status: bug.status ?? "",
    resolution: bug.resolution ?? "",
    product: bug.product ?? "",
    component: bug.component ?? "",
    assignee: bug.assigned_to ?? "",
    assignee_name: who.real_name || who.nick || "",
    triage_owner: bug.triage_owner ?? "",
    triage_owner_name: bug.triage_owner_detail?.real_name || bug.triage_owner_detail?.nick || "",
    severity: bug.severity ?? "",
    last_change_time: bug.last_change_time ?? "",
    // Hidden in a security group (core-security, dom-core-security, ...).
    security: (bug.groups ?? []).some((g) => g.includes("security")),
    // One entry per check filter: whether this bug is in it.
    filters: filters.map((f) => f.fields.some((x) => ["fixed", "verified"].includes(bug[x]))),
    // Bugzilla omits a custom flag from REST responses when it is unset.
    // The field was requested, so absent means "---", not "unknown".
    flags: Object.fromEntries(columns.map((c) => {
      const v = DERIVED[c] ? DERIVED[c].value(bug) : bug[c];
      return [c, Array.isArray(v) ? (v.length ? v.join(", ") : "---") : (v ?? "---")];
    })),
  };
}

async function execute(check, key, opts, fresh, phabToken) {
  const full = [...bugzilla.BASE_FIELDS, ...fieldsFor(check)];
  let bugs;
  let candidates;
  let extra = {};
  if (check.source) {
    [bugs, extra] = await check.source.fetch(full, key, opts, fresh, phabToken);
    candidates = extra.candidates;
  } else {
    // With post-filters the rows are candidates, not answers: ask only for
    // what the filters need, then fetch the survivors properly.
    const need = check.post.length ? [...new Set(check.post.flatMap((p) => p.fields))].sort() : full;
    bugs = await bugzilla.search(check.query.restParams(), need, key);
    candidates = bugs.length;
    if (candidates >= BUGZILLA_ROW_CAP && !check.post.length) {
      // The table is truncated, but the count need not be.
      extra.total = await bugzilla.count(check.query.restParams(), key);
    }
  }
  if (check.post.length) {
    for (const step of check.post) bugs = await step.apply(bugs, key, fresh);
    bugs = bugs.length ? await bugzilla.byIds(bugs.map((b) => b.id), full, key) : [];
  }
  bugs = [...bugs].sort((a, b) =>
    (a.last_change_time ?? "").localeCompare(b.last_change_time ?? "") || a.id - b.id);
  const ids = [...new Set(bugs.map((b) => b.id).filter(Boolean))];
  return {
    state: "ok",
    count: bugs.length,
    candidates,
    capped: candidates >= BUGZILLA_ROW_CAP,
    bugs: bugs.map((b) => shape(b, check)),
    ids_url: ids.length > 0 && ids.length <= IDS_URL_MAX ? idsUrl(ids) : null,
    ran_at: now(),
    ...extra,
  };
}

// `fresh` skips every cached result this check depends on, not just its own.
export async function run(check, key, opts = {}, { fresh = false, phabToken = "" } = {}) {
  opts = check.source ? check.source.parse(opts) : {};
  if (check.needsKey && !key) return { state: "needs_key", count: null };
  if (check.needsPhabricator && !phabToken) return { state: "needs_phab", count: null };
  if (!check.needsPhabricator) phabToken = ""; // so it never splits the cache of checks that don't use it
  const cacheKey = ["check", key ?? "", phabToken, check.id, check.query.buglistUrl(), opts];
  try {
    return await cache.getOrRun(cacheKey, () => execute(check, key, opts, fresh, phabToken), undefined, fresh);
  } catch (e) {
    if (e instanceof Unavailable) return { state: "unavailable", count: null, message: e.message };
    if (e instanceof SourceError) return { state: "error", count: null, message: e.message };
    // One failed query fails its own row, not the page.
    return { state: "error", count: null, message: `${e.name}: ${e.message}` };
  }
}

const title = (c) => (c.channel ? `${c.channel.label} · ${c.title}` : c.title);

// Distinct bugs across the given checks. The same bug can sit in several
// queues, so summing the rows would overstate the work.
export async function headline(checks, key) {
  const results = await Promise.all(checks.map((c) => run(c, key)));
  const ids = new Set();
  const incomplete = [];
  results.forEach((r, i) => {
    if (r.state === "ok") {
      for (const b of r.bugs) ids.add(b.id);
      if (r.capped) incomplete.push({ title: title(checks[i]), reason: "hit Bugzilla's row cap" });
    } else {
      const reason = r.state === "needs_key" ? "needs an API key" : (r.message ?? r.state);
      incomplete.push({ title: title(checks[i]), reason });
    }
  });
  return {
    count: ids.size,
    sum_of_rows: results.reduce((s, r) => s + (r.count ?? 0), 0),
    checks: results.length,
    incomplete,
  };
}
