// Open Phabricator revisions for a branch whose uplift is missing or denied.
//
// A revision for Beta, Release or an ESR waits on its bug's approval flag
// (approval-mozilla-beta and so on), which lives on the revision's attachment
// in Bugzilla. Without one, "Uplift requested" never shows the revision, so the
// uplift is a decision nobody knows is waiting. With the flag denied, the
// revision should not be in progress at all. An approved revision still open
// on a bug already fixed on the branch is a leftover, usually one that landed,
// was backed out and was replaced by another revision, and was never abandoned.
//
// Phabricator knows which branch a revision targets; only Bugzilla has the
// flags. Bugzilla's own revision list hides the branch of every security
// revision, so the revisions come from Phabricator, as the reader.

import * as bugzilla from "./bugzilla.js";
import { cache } from "./http.js";
import { openRevisions } from "./phabricator.js";

// Table columns this check adds; they come from Phabricator, not Bugzilla.
export const REVISION_COLUMNS = ["revision", "revision_status", "revision_changed", "problem"];

export class UpliftRevisions {
  options = null;

  constructor(channel) {
    this.repo = `firefox-${channel.key}`; // firefox-beta, firefox-esr140
    this.flag = channel.approvalFlag;
    this.statusField = channel.statusField;
  }

  parse() {
    return {};
  }

  lines() {
    return [
      `open revisions (needs review, accepted, needs revision) in Phabricator's ${this.repo} repository`,
      `then: each one's ${this.flag} flag, on its revision attachment in Bugzilla`,
      "shows: no uplift request, uplift denied, approved but the bug is already fixed on this branch, " +
        "a request for another branch, no bug linked, or a bug hidden from your account",
    ];
  }

  // [rows, extra result fields]. One row per revision with a problem.
  async fetch(fields, key, opts, fresh, phabToken) {
    const revisions = await cache.getOrRun(["phab-open", this.repo, phabToken],
      () => openRevisions(this.repo, phabToken), undefined, fresh);
    const ids = [...new Set(revisions.map((r) => r.bug).filter(Boolean))];
    // A search by id leaves out bugs this key can't see, rather than failing.
    const bugFields = fields.filter((f) => !REVISION_COLUMNS.includes(f));
    const bugs = new Map((ids.length ? await bugzilla.byIds(ids, bugFields, key) : []).map((b) => [b.id, b]));
    const attachments = bugs.size ? await bugzilla.attachments([...bugs.keys()], key) : new Map();

    const rows = [];
    for (const rev of revisions) {
      const problem = this.problem(rev, bugs, attachments);
      if (!problem) continue;
      const bug = bugs.get(rev.bug) ?? { id: rev.bug, summary: rev.bug ? "" : rev.title };
      rows.push({ ...bug, revision: `D${rev.id}`, revision_status: rev.status, revision_changed: rev.changed, problem });
    }
    return [rows, { candidates: revisions.length, lines: this.lines() }];
  }

  problem(rev, bugs, attachments) {
    if (!rev.bug) return "no bug linked";
    if (!bugs.has(rev.bug)) return "bug hidden from your account";
    const attachment = (attachments.get(rev.bug) ?? [])
      .find((a) => a.file_name === `phabricator-D${rev.id}-url.txt`);
    if (!attachment) return "revision not attached to its bug";
    const approvals = attachment.flags.filter((f) => f.name.startsWith("approval-mozilla-"));
    const mine = approvals.filter((f) => f.name === this.flag).map((f) => f.status);
    if (mine.includes("+") && ["fixed", "verified"].includes(bugs.get(rev.bug)[this.statusField])) {
      return "approved, but bug already fixed on this branch";
    }
    if (mine.includes("?") || mine.includes("+")) return null; // requested, or approved and waiting to land: fine
    if (mine.includes("-")) return "uplift denied";
    if (approvals.length) return `requested for ${approvals.map((f) => f.name.replace("approval-mozilla-", "")).join(", ")} instead`;
    return "no uplift request";
  }
}
