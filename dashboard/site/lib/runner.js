// Run checks and shape their results.
//
// A result's `state` is one of:
//   ok           count is real (it may be zero)
//   needs_key    the check is meaningless without an API key, so it did not run
//   unavailable  a cross-check source failed; there is deliberately no count
//   error        the query itself failed

import * as bugzilla from "./bugzilla.js";
import { BUGZILLA_ROW_CAP } from "./config.js";
import { SourceError, cache } from "./http.js";
import { Unavailable } from "./postfilters.js";
import { idsUrl } from "./querydef.js";

const IDS_URL_MAX = 400; // past this many ids the bug_id link gets too long to open (BugDash's limit too)

const now = () => new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

function shape(bug, columns) {
  const who = bug.assigned_to_detail ?? {}; // Bugzilla sends it with assigned_to
  return {
    id: bug.id,
    summary: bug.summary ?? "",
    status: bug.status ?? "",
    resolution: bug.resolution ?? "",
    product: bug.product ?? "",
    component: bug.component ?? "",
    assignee: bug.assigned_to ?? "",
    assignee_name: who.real_name || who.nick || "",
    severity: bug.severity ?? "",
    last_change_time: bug.last_change_time ?? "",
    // Hidden in a security group (core-security, dom-core-security, ...).
    security: (bug.groups ?? []).some((g) => g.includes("security")),
    // Bugzilla omits a custom flag from REST responses when it is unset.
    // The field was requested, so absent means "---", not "unknown".
    flags: Object.fromEntries(columns.map((c) => [c, Array.isArray(bug[c])
      ? (bug[c].length ? bug[c].join(", ") : "---") : (bug[c] ?? "---")])),
  };
}

async function execute(check, key, opts, fresh) {
  const full = [...bugzilla.BASE_FIELDS, ...check.columns];
  let bugs;
  let candidates;
  let extra = {};
  if (check.source) {
    [bugs, extra] = await check.source.fetch(full, key, opts, fresh);
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
  return {
    state: "ok",
    count: bugs.length,
    candidates,
    capped: candidates >= BUGZILLA_ROW_CAP,
    bugs: bugs.map((b) => shape(b, check.columns)),
    ids_url: bugs.length > 0 && bugs.length <= IDS_URL_MAX ? idsUrl(bugs.map((b) => b.id)) : null,
    ran_at: now(),
    ...extra,
  };
}

// `fresh` skips every cached result this check depends on, not just its own.
export async function run(check, key, opts = {}, { fresh = false } = {}) {
  opts = check.source ? check.source.parse(opts) : {};
  if (check.needsKey && !key) return { state: "needs_key", count: null };
  const cacheKey = ["check", key ?? "", check.id, check.query.buglistUrl(), opts];
  try {
    return await cache.getOrRun(cacheKey, () => execute(check, key, opts, fresh), undefined, fresh);
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
