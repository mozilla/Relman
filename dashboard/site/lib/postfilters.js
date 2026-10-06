// Steps that run on a Bugzilla result and cannot be expressed as query parameters.
//
// Every one of these *subtracts* bugs. If a step cannot complete it throws
// Unavailable, and the row says so: falling back to the unfiltered result would
// report problems that do not exist.

import * as bugzilla from "./bugzilla.js";
import { fetchCommits, landed } from "./commits.js";
import { SourceError, cache } from "./http.js";

// A cross-check could not be completed; the row has no trustworthy number.
export class Unavailable extends Error {}

// Keep bugs with a live approval granted since the cycle began.
//
// `flagtypes.name changedafter` is true when *any* flag changed after the
// date, and mainline approval flags carry no version, so the query alone
// cannot tell this cycle's approval from the last one.
export class ApprovedThisCycle {
  line = "drop bugs whose approval is on an obsolete patch, or was granted in an earlier cycle";
  fields = ["id"];

  constructor(flag, since) {
    this.flag = flag;
    this.since = since;
  }

  async apply(bugs, key) {
    if (!bugs.length) return bugs;
    let atts;
    try {
      atts = await bugzilla.attachments(bugs.map((b) => b.id), key);
    } catch (e) {
      if (e instanceof SourceError) throw new Unavailable(`could not read attachment flags: ${e.message}`);
      throw e;
    }
    const since = new Date(this.since);
    const live = (id) => (atts.get(id) ?? []).some((a) => !a.is_obsolete && (a.flags ?? []).some((f) =>
      f.name === this.flag && f.status === "+" && new Date(f.modification_date) >= since));
    return bugs.filter((b) => live(b.id));
  }
}

// Bugs landed on `branch` since `since`, and not since backed out.
//
// Fetched whole each time the cache lapses (or a row is refreshed), never from
// an incremental index: a stale index reports the day's landings as never landed.
function landedBugIds(branch, since, fresh) {
  return cache.getOrRun(["landed", branch, since],
    async () => new Set(landed(await fetchCommits(branch, since)).keys()), undefined, fresh);
}

export class NotLandedOnBranch {
  line = "drop bugs named by a commit on this channel's branch";
  fields = ["id"];

  constructor(branch, since) {
    this.branch = branch;
    this.since = since;
  }

  async apply(bugs, key, fresh) {
    if (!bugs.length) return bugs;
    let ids;
    try {
      ids = await landedBugIds(this.branch, this.since, fresh);
    } catch (e) {
      if (e instanceof SourceError) throw new Unavailable(`commit history for ${this.branch} unavailable: ${e.message}`);
      throw e;
    }
    return bugs.filter((b) => !ids.has(b.id));
  }
}

const STAFF_DOMAINS = ["@mozilla.com", "@mozilla.org", "@mozillafoundation.org"];

// Stand-in for the %group.x% reporter filters, which REST rejects (error 804).
export class ReporterNotStaff {
  line = "drop reporters at mozilla.com / mozilla.org / mozillafoundation.org / softvision (REST rejects %group.x%)";
  fields = ["id", "creator"];

  async apply(bugs) {
    if (bugs.some((b) => !(b.creator ?? "").includes("@"))) {
      throw new Unavailable("Bugzilla did not return reporter email addresses, so staff cannot be filtered out");
    }
    const staff = (email) => {
      email = email.toLowerCase();
      return STAFF_DOMAINS.some((d) => email.endsWith(d)) || email.includes("softvision");
    };
    return bugs.filter((b) => !staff(b.creator));
  }
}
