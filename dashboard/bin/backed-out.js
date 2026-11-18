#!/usr/bin/env node
// Bugs backed out of main and resolved as fixed, read from a local Firefox
// checkout instead of GitHub, so the window can be the whole Nightly cycle.
// The same rules as the dashboard's nightly.backed-out-resolved check
// (commits.js), for the nightly-backout-audit skill, except that whether a bug
// still has a landing is read from its whole history, not just the window.
//
//   node dashboard/bin/backed-out.js --repo /path/to/firefox [--since 2026-11-09T16:00:00Z]
//
// --since defaults to the start of the current Nightly cycle. Reads
// origin/main as it is, so fetch first. Prints JSON. Every bug resolved as
// fixed with a backout in the window that was not itself reverted is listed,
// with `live_landing` true when the bug still has a landing in main (relanded,
// or another part never backed out) and false when nothing of it is left, and
// the bug's own landings and backouts over its whole history, oldest first,
// each revert with the sha it reverts.
// The Bugzilla key comes from $BUGZILLA_API_KEY or ~/.config/bugzilla/api-key,
// as for check.js.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";

import { QUERY } from "../site/lib/backed_out_resolved.js";
import * as bugzilla from "../site/lib/bugzilla.js";
import { backedOut, classify, landed } from "../site/lib/commits.js";
import * as versions from "../site/lib/versions.js";

const args = process.argv.slice(2);
const arg = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const repo = arg("--repo");
if (!repo) {
  console.error("usage: backed-out.js --repo /path/to/firefox [--since <ISO time>]");
  process.exit(1);
}

const releases = await versions.load();
const since = arg("--since") ?? releases.cycleStart;
const git = (...a) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8", maxBuffer: 1 << 30 });

// Committer time as UTC, so dates compare as strings the way commits.js expects.
const log = (...a) => git("log", "origin/main", ...a, "--format=%H%x00%ct%x00%B%x01")
  .split("\x01").map((s) => s.replace(/^\n/, "")).filter(Boolean)
  .map((s) => {
    const [sha, ct, message] = s.split("\x00");
    return { sha, date: new Date(Number(ct) * 1000).toISOString().replace(/\.\d{3}Z$/, "Z"), message };
  });

const commits = log(`--since=${since}`);
const out = backedOut(commits);
const ids = [...out.keys()].sort((a, b) => a - b);
// Whether a bug still has a landing is read from its whole history, not just
// the window: a part that landed before `since` and was never backed out still
// counts. classify() drops the commits that only contain the number.
const history = ids.length ? log("-F", ...ids.map((id) => `--grep=${id}`)) : [];
const live = landed(history);
const ownCommits = (bug) => history.filter((c) => classify(c.message)[1].includes(bug)).reverse().map((c) => ({
  sha: c.sha.slice(0, 12),
  date: c.date,
  kind: classify(c.message)[0],
  subject: c.message.split("\n", 1)[0],
  reverts: c.message.match(/^This reverts commit ([0-9a-f]{12})/m)?.[1] ?? null,
}));
const keyFile = () => {
  try { return readFileSync(`${homedir()}/.config/bugzilla/api-key`, "utf8").trim(); } catch { return ""; }
};
const key = process.env.BUGZILLA_API_KEY?.trim() || keyFile();
const fields = [...bugzilla.BASE_FIELDS, releases.nightly.statusField, "cf_last_resolved"];
const rows = [];
for (let i = 0; i < ids.length; i += 300) {
  rows.push(...await bugzilla.search([...QUERY.restParams(), ["id", ids.slice(i, i + 300).join(",")]], fields, key));
}

console.log(JSON.stringify({
  since,
  head: git("log", "-1", "--format=%h %cs", "origin/main").trim(),
  commits: commits.length,
  backed_out: ids.length,
  key_used: Boolean(key),
  nightly_status_field: releases.nightly.statusField,
  bugs: rows.sort((a, b) => a.id - b.id).map((b) => ({
    id: b.id,
    summary: b.summary,
    resolution: b.resolution,
    last_resolved: b.cf_last_resolved ?? null,
    nightly_status: b[releases.nightly.statusField] ?? "---",
    security: (b.groups ?? []).some((g) => g.includes("security")),
    backed_out: out.get(b.id),
    live_landing: live.has(b.id),
    commits: ownCommits(b.id),
  })),
}, null, 1));
