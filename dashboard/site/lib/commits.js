// Commits on mozilla-firefox/firefox, from GitHub, and the bugs they name.
//
// The summary line of a landing starts with its bugs: "Bug 123 - ...",
// "Bug 123: ...", "Bug 123, Bug 456 - ...". A backout is a revert of one:
// 'Revert "Bug 123 - ..." for causing ...'. It names the same bug, and it is
// the opposite of a landing, so the two are told apart before anything counts.

import { GITHUB_REPO } from "./config.js";
import { SourceError, get } from "./http.js";

const LANDING = /^\s*((?:bug\s*\d+\s*(?:,|&|and)?\s*)+)/i;
const BACKOUT = /^\s*(revert\b|back(ed|ing)?\s*out\b)/i;
const BUG = /\bbug\s*(\d+)/gi;
const REVERTS = /^This reverts commit ([0-9a-f]{40})/m;

const bugsIn = (text) => [...text.matchAll(BUG)].map((m) => Number(m[1]));

// ["landing" | "backout" | null, bug numbers] for one commit message.
export function classify(message) {
  const line = message.split("\n", 1)[0];
  if (BACKOUT.test(line)) return ["backout", bugsIn(line)];
  const m = line.match(LANDING);
  if (m) return ["landing", bugsIn(m[1])];
  return [null, []];
}

// Bugs with a landing in `commits` that has not been backed out, with the
// time of the latest such landing (a Map of bug -> ISO date).
//
// A git revert names the one commit it undoes ("This reverts commit <sha>."),
// so backing out one part of a multi-part bug leaves the other parts landed.
// A backout that names no sha (the older "Backed out changeset" form) undoes
// every earlier landing of its bugs. Reverting a revert restores the landing.
// `commits` may be in any order.
export function landed(commits) {
  const ordered = [...commits].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
  const undone = new Set();
  // Newest first, so a revert that was itself reverted undoes nothing.
  for (const c of [...ordered].reverse()) {
    if (undone.has(c.sha)) continue;
    const m = c.message.match(REVERTS);
    if (m) undone.add(m[1]);
  }
  const state = new Map(); // bug -> dates of its live landings
  for (const c of ordered) {
    if (undone.has(c.sha)) continue;
    const [kind, bugs] = classify(c.message);
    for (const b of bugs) {
      if (kind === "landing") state.set(b, [...(state.get(b) ?? []), c.date]);
      else if (kind === "backout" && !REVERTS.test(c.message)) state.delete(b);
    }
  }
  return new Map([...state].map(([b, dates]) => [b, dates.at(-1)]));
}

// Every commit on `branch` with a committer date at or after `since`.
//
// Throws rather than returning a partial list: every check built on this
// subtracts or adds bugs by what it finds, so a missing tail is a wrong answer.
export async function fetchCommits(branch, since, maxPages = 30) {
  const url = `https://api.github.com/repos/${GITHUB_REPO}/commits`;
  const out = [];
  for (let page = 1; page <= maxPages; page++) {
    const params = [["sha", branch], ["since", since], ["per_page", "100"], ["page", String(page)]];
    const r = await get(url, { params, headers: { Accept: "application/vnd.github+json" } });
    if (r.status !== 200) {
      const detail = await r.json().then((b) => b.message ?? "", () => "");
      throw new SourceError(`GitHub answered HTTP ${r.status} ${detail}`.trim());
    }
    for (const c of await r.json()) {
      out.push({ sha: c.sha, date: c.commit.committer.date, message: c.commit.message });
    }
    if (!(r.headers.get("link") ?? "").includes('rel="next"')) return out;
  }
  throw new SourceError(`more than ${maxPages * 100} commits on ${branch} since ${since}; history incomplete`);
}
