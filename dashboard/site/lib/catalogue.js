// The query catalogue: every check on every page.
//
// Field names are derived from the channel (see versions.Channel), never typed
// out, so the catalogue moves with the trains.

import { ApprovedThisCycle, NotLandedOnBranch, ReporterNotStaff } from "./postfilters.js";
import { Changed, Chart, Eq, In, Keywords, Query, allOf, anyOf } from "./querydef.js";
import { LANDED_COLUMN, QUERY as SEC_OPEN_QUERY, SecOpenOnMain } from "./sec_open_on_main.js";

// tier -> [name, subtitle, runs on load]
const TIERS = {
  1: ["Tier 1", "What needs a decision today", true],
  2: ["Tier 2", "The state of the release", true],
  3: ["Tier 3", "Everything else, on demand", false],
  misc: ["Misc", "Ongoing, on demand", false],
  page: ["Misc", "Not channel-specific", false],
};

const HIGH_IMPACT_PRODUCTS = [
  "Core", "DevTools", "External Software Affecting Firefox", "Fenix", "Firefox",
  "Firefox for Android", "Geckoview", "NSPR", "NSS", "Toolkit", "WebExtensions",
];
const HIGH_IMPACT_KEYWORDS = "crash regression leak topcrash assertion dataloss";
const USER_BUG_PRODUCTS = [
  "Core", "DevTools", "External Software Affecting Firefox", "Fenix", "Firefox",
  "Firefox Build System", "Firefox for Android", "Firefox for Echo Show",
  "Firefox for FireTV", "Firefox for iOS", "Focus", "Focus-iOS", "Geckoview",
  "NSPR", "NSS", "Toolkit", "WebExtensions",
];
const NOT_FIXED = "fixed,wontfix,disabled,verified";
const BAD_RESOLUTIONS = "INVALID,WORKSFORME,DUPLICATE";
const NOBODY = "nobody@mozilla.org";
// Products with no ESR versions.
const ESR_LESS_PRODUCTS = ["Firefox for Android", "Data Platform and Tools"];

class Check {
  constructor(id, title, tier, query, channel = null, {
    needsKey = false, shouldBeZero = false, post = [], notes = [],
    columns = [], // extra table columns, mostly status and tracking flags
    source = null, // replaces the single Bugzilla search (see sec_open_on_main.js)
    // Claude Code skills that go deeper on this check's bugs: [{ name, summary }].
    // Each lives in .claude/skills/<name>/ at the repo root and gets its bugs
    // from bin/check.js, so the query stays here.
    skills = [],
  } = {}) {
    Object.assign(this, { id, title, tier, query, channel, needsKey, shouldBeZero, post, notes, columns, source, skills });
  }

  get runsOnLoad() {
    return TIERS[this.tier][2];
  }

  lines() {
    if (this.source) return this.source.lines({});
    const out = [...this.query.lines(), ...this.post.map((p) => `then: ${p.line}`)];
    const extra = this.query.linkOnlyLines();
    if (extra.length) out.push(`the “Open query in Bugzilla” link adds: ${extra.join(", ")}`);
    return out;
  }

  // A sourced check's query depends on what the source found, so it has no link.
  get queryUrl() {
    return this.source ? null : this.query.buglistUrl();
  }
}

const status = (v) => `cf_status_firefox${v}`;

// --- Tier 1 -----------------------------------------------------------------

const trackingRequested = (c) => new Query([Chart(c.trackingField, "equals", "?")]);

const relnoteRequests = (c) => new Query([
  Chart("cf_tracking_firefox_relnote", "equals", "?"),
  Chart(c.statusField, "anywords", "fixed,verified"),
  Chart(status(c.version - 1), "nowords", "fixed,verified"),
]);

const upliftRequested = (c) => new Query([Chart("flagtypes.name", "substring", `${c.approvalFlag}?`)]);

const secFixedOnNightly = (r) => [
  Eq("resolution", "FIXED"),
  Chart(r.nightly.statusField, "anywordssubstr", "verified,fixed"),
  Chart("bug_group", "substring", "sec"),
];

const olderThanNightly = (r) => [r.beta, r.release, ...r.esrs];

function secPendingTracking(r) {
  const older = olderThanNightly(r);
  return new Query([...secFixedOnNightly(r),
    anyOf(
      Chart(r.nightly.trackingField, "equals", "---"),
      ...older.map((c) => Chart(c.statusField, "equals", "---")),
      ...older.map((c) => allOf(
        Chart(c.statusField, "anyexact", "affected,fix-optional"),
        Chart(c.trackingField, "equals", "---"),
      )),
    ),
  ]);
}

const secPendingAffected = (r) => new Query([...secFixedOnNightly(r),
  anyOf(...olderThanNightly(r).map((c) => Chart(c.statusField, "equals", "---"))),
]);

// --- Tier 2 -----------------------------------------------------------------

const regressions = (c, previousValues) => new Query([
  Eq("resolution", "---"),
  Keywords("regression"),
  Chart(c.statusField, "equals", "affected"),
  previousValues.length === 1
    ? Chart(status(c.version - 1), "equals", previousValues[0])
    : anyOf(...previousValues.map((v) => Chart(status(c.version - 1), "equals", v))),
  Chart(c.trackingField, "notequals", "-"),
  Chart("keywords", "nowordssubstr", "stalled,intermittent-failure"),
]);

const newRegressions = (c) => regressions(c, ["unaffected", "?", "---"]);
const carryoverRegressions = (c) => regressions(c, ["affected", "wontfix", "disabled", "fix-optional"]);
// The carry-overs not enabled in the previous Release: their regressor was
// gated off there, so the question is whether it still is on this branch.
const carryoverDisabledInRelease = (c) => regressions(c, ["disabled"]);

const openRegressionsEsr = (c) => new Query([
  In("bug_status", ["UNCONFIRMED", "NEW", "ASSIGNED", "REOPENED"]),
  Keywords("regression"),
  Chart(c.statusField, "equals", "affected"),
  Chart("keywords", "nowords", "stalled,intermittent-failure"),
]);

const trackingNotFixed = (c) => new Query([
  Chart(c.trackingField, "anywordssubstr", "+,blocking"),
  Chart(c.statusField, "nowordssubstr", NOT_FIXED),
  Chart("resolution", "nowordssubstr", BAD_RESOLUTIONS),
]);

const trackingNotFixedEsr = (c, mainline) => new Query([
  Chart(c.trackingField, "anywordssubstr", `${mainline}+`),
  Chart(c.statusField, "nowordssubstr", NOT_FIXED),
  Chart("resolution", "nowordssubstr", BAD_RESOLUTIONS),
]);

const burndown = (c) => new Query([
  Eq("resolution", "FIXED"),
  Chart(c.statusField, "equals", "affected"),
  Chart("flagtypes.name", "notsubstring", c.approvalFlag),
]);

const secHighPendingUplift = (r) => new Query([
  Chart("keywords", "anywordssubstr", "sec-critical sec-high"),
  Chart(r.nightly.statusField, "anywordssubstr", "fixed,verified"),
  Chart(r.beta.statusField, "equals", "affected"),
  Chart("flagtypes.name", "notsubstring", r.beta.approvalFlag),
]);

// --- Tier 3 -----------------------------------------------------------------

const fixedFixOptionals = (c) => new Query([Eq("resolution", "FIXED"), Chart(c.statusField, "equals", "fix-optional")]);

const s2Regressions = (c) => new Query([
  Eq("resolution", "---"),
  Eq("bug_severity", "S2"),
  Keywords("regression"),
  Chart(c.statusField, "equals", "affected"),
]);

const unassigned = (q) => q.extended(Chart("assigned_to", "equals", NOBODY));

const upliftApprovedNotFixed = (c, since) => new Query([
  Chart("flagtypes.name", "substring", `${c.approvalFlag}+`),
  Chart(c.statusField, "nowords", "fixed,verified,wontfix,disabled,unaffected"),
  Chart("flagtypes.name", "changedafter", since),
  Chart("product", "nowords", "Thunderbird,NSS"),
  // No ESR status field on these, so "not fixed" always matches. One row
  // each: nowords would split "Firefox for Android" and drop all of Firefox.
  ...(c.isEsr ? ESR_LESS_PRODUCTS.map((p) => Chart("product", "notequals", p)) : []),
]);

const approvedNeverLanded = (c, since) => new Query([
  Chart("flagtypes.name", "substring", `${c.approvalFlag}+`),
  Chart(c.statusField, "equals", "fixed"),
  Chart(c.statusField, "changedafter", since),
  Chart("product", "nowords", "Thunderbird,NSS"),
]);

const secResolvedMissingVersion = (r) => new Query([
  Changed("resolution", r.cycleStart, "FIXED"),
  Chart("bug_group", "substring", "sec"),
  Chart(r.nightly.statusField, "equals", "---"),
  // One row per product: nowords splits on spaces too, so
  // "MailNews Core" would also exclude Core.
  ...["NSS", "MailNews Core", "Thunderbird"].map((p) => Chart("product", "notequals", p)),
]);

// --- Misc tier ----------------------------------------------------------------

const highImpactForBeta = (r) => new Query([
  Eq("resolution", "FIXED"),
  In("product", HIGH_IMPACT_PRODUCTS),
  In("classification", ["Client Software", "Components"]),
  Chart("keywords", "anywords", HIGH_IMPACT_KEYWORDS),
  Chart(r.nightly.statusField, "anywordssubstr", "verified,fixed"),
  Chart(r.beta.statusField, "equals", "---"),
  Chart("keywords", "nowords", "intermittent-failure"),
]);

const highImpactMissingNightly = (r) => new Query([
  In("product", HIGH_IMPACT_PRODUCTS),
  In("classification", ["Client Software", "Components"]),
  Changed("resolution", r.cycleStart, "FIXED"),
  Chart("keywords", "anywords", HIGH_IMPACT_KEYWORDS),
  Chart(r.nightly.statusField, "equals", "---"),
  Chart("keywords", "nowords", "intermittent-failure"),
]);

const relnoteNightlyPlus = () => new Query([Chart("cf_tracking_firefox_relnote", "equals", "nightly+")]);

const relnotePlus = (c) => new Query([
  Chart("cf_tracking_firefox_relnote", "equals", `${c.version}+`),
  Chart(c.statusField, "anywords", "affected,fixed,verified,fix-optional"),
  Chart(c.statusField, "notsubstring", "disabled"),
]);

// --- Misc page ----------------------------------------------------------------

const filedTodayByUsers = () => new Query(
  [
    Eq("resolution", "---"),
    In("product", USER_BUG_PRODUCTS),
    In("classification", ["Client Software", "Developer Infrastructure", "Components"]),
    Changed("[Bug creation]", "-24h"),
    Chart("reporter", "notequals", "intermittent-bug-filer@mozilla.bugs"),
  ],
  [
    Chart("reporter", "notequals", "%group.editbugs%"),
    Chart("reporter", "notequals", "%group.mozilla-corporation%"),
  ],
);

// --- Pages ------------------------------------------------------------------

export const PAGES = ["nightly", "beta", "release", "esr", "misc"];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// 2026-09-24T16:00:00+00:00 -> 24 Sep 2026, 16:00 UTC
function readable(iso) {
  const d = new Date(iso);
  const hhmm = d.toISOString().slice(11, 16);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}, ${hhmm} UTC`;
}

// Every check for one channel, in display order within each tier.
function channelChecks(c, r) {
  const k = c.kind;
  const mainline = !c.isEsr;
  const since = r.cycleStart;
  const out = [];
  const add = (slug, title, tier, query, opts = {}) => {
    out.push(new Check(`${c.key}.${slug}`, title, tier, query, c,
      { columns: [c.statusField, c.trackingField], ...opts }));
  };

  // Tier 1
  add("tracking-requested", "Tracking requested", "1", trackingRequested(c));
  if (mainline) add("relnote-requests", "Release Note Requests", "1", relnoteRequests(c));
  if (k !== "nightly") add("uplift-requested", "Uplift requested", "1", upliftRequested(c));
  if (k === "beta") {
    const secCols = r.all.map((ch) => ch.statusField);
    add("sec-pending-tracking", "Security Bugs pending tracking", "1",
      secPendingTracking(r), { needsKey: true, columns: secCols });
    add("sec-pending-affected", "Security Bugs pending affected versions", "1",
      secPendingAffected(r), { needsKey: true, columns: secCols });
  }

  // Tier 2
  if (mainline) {
    add("new-regressions", "New Regressions", "2", newRegressions(c));
    add("carryover-regressions", "Carry Over Regressions", "2", carryoverRegressions(c));
    add("tracking-not-fixed", "Tracking+ not fixed", "2", trackingNotFixed(c));
  } else {
    add("open-regressions", "Open regressions", "2", openRegressionsEsr(c));
    for (const m of r.mainline) {
      add(`tracking-not-fixed-${m.version}`, `Tracking+ not fixed (${m.version}+)`, "2",
        trackingNotFixedEsr(c, m.version));
    }
  }
  if (k !== "nightly") add("burndown", "Burndown (no uplift requested)", "2", burndown(c));
  if (k === "beta") {
    add("sec-high-pending-uplift", "Sec-high pending uplift request", "2", secHighPendingUplift(r),
      { needsKey: true, shouldBeZero: true, columns: [r.nightly.statusField, c.statusField] });
  }

  // Tier 3
  if (k === "beta" || k === "release") add("fixed-fix-optionals", "Fixed fix-optionals", "3", fixedFixOptionals(c));
  if (mainline) add("tracking-unassigned", "Tracking+ unassigned", "3", unassigned(trackingNotFixed(c)));
  add("s2-regressions", "S2 regressions not fixed", "3", s2Regressions(c));
  if (mainline) add("s2-unassigned", "S2 regressions unassigned", "3", unassigned(s2Regressions(c)));
  if (k === "beta") {
    add("carryover-disabled-previous", "Carry over regressions, disabled in previous version", "3",
      carryoverDisabledInRelease(c), {
        columns: [c.statusField, status(c.version - 1), c.trackingField, "regressed_by"],
        skills: [{
          name: "beta-carryover-disabled-audit",
          summary: "whether each regressor is still off for Release in the current Beta code, and if not, the bug that turned it on",
        }],
      });
  }
  if (k !== "nightly") {
    const approval = new ApprovedThisCycle(c.approvalFlag, since);
    add("uplift-approved-not-fixed", "Uplift approved, version not fixed", "3",
      upliftApprovedNotFixed(c, since), { shouldBeZero: true, post: [approval] });
    add("approved-never-landed", "Fixed, uplift approved, never landed", "3",
      approvedNeverLanded(c, since), {
        shouldBeZero: true,
        post: [approval, new NotLandedOnBranch(c.branch, since)],
        notes: [
          `Only commits since this cycle began (${readable(since)}) are checked. An ` +
          "uplift that landed before then, but had its approval flag edited later, " +
          "shows up here even though it did land.",
          "Checked per bug, not per patch: if a bug has two approved patches and " +
          "only one landed, the bug counts as landed and does not show up here.",
        ],
      });
  }
  if (k === "beta") {
    add("sec-resolved-missing-version", "Security bugs resolved but missing a version", "3",
      secResolvedMissingVersion(r), { needsKey: true, columns: [r.nightly.statusField] });
    add("sec-open-on-main", "Security fixes on main, bug still open", "3", SEC_OPEN_QUERY, {
      needsKey: true,
      source: SecOpenOnMain,
      columns: [r.nightly.statusField, c.statusField, LANDED_COLUMN],
      notes: [
        "If the fix is complete, resolve the bug: the uplift request, status " +
        "flags and advisory all unblock from there. If it is not (a partial " +
        "landing, a follow-up still coming), the bug is correctly open and the " +
        "row clears itself when the rest lands.",
        "A landing that was backed out does not count; if only one part of a " +
        "bug was backed out, the other parts still do.",
      ],
    });
  }

  // Misc tier
  if (k === "beta") {
    add("high-impact-for-beta", "High-impact fixes for potential Beta uplift", "misc",
      highImpactForBeta(r), { columns: [r.nightly.statusField, c.statusField] });
  }
  if (k === "nightly") {
    add("high-impact-missing-nightly", "Recently resolved high impact fixes missing nightly version", "misc",
      highImpactMissingNightly(r));
    add("relnote-nightly-plus", "Release Notes Nightly+", "misc", relnoteNightlyPlus(), {
      columns: ["cf_tracking_firefox_relnote", c.statusField],
      skills: [{
        name: "nightly-relnote-audit",
        summary: "which of these are no longer Nightly-only, and the bug that changed each one",
      }],
    });
  }
  if (mainline) {
    add("relnote-plus", "Release Notes +", "misc", relnotePlus(c),
      { columns: ["cf_tracking_firefox_relnote", c.statusField] });
  }
  return out;
}

const miscPageChecks = () => [
  new Check("misc.filed-today", "Bugs filed today by users", "page", filedTodayByUsers(), null, {
    post: [new ReporterNotStaff()],
    notes: [
      "Approximate: REST rejects the %group.editbugs% and " +
      "%group.mozilla-corporation% reporter exclusions, so this panel drops " +
      "reporters by email domain instead. The “Open query in Bugzilla” link " +
      "uses the group exclusions, so the two can differ.",
    ],
  }),
];

export function pageChannels(page, r) {
  if (page === "esr") return r.esrs;
  if (["nightly", "beta", "release"].includes(page)) return [r[page]];
  return [];
}

export function pageChecks(page, r) {
  if (page === "misc") return miscPageChecks();
  return pageChannels(page, r).flatMap((c) => channelChecks(c, r));
}

export function allChecks(r) {
  return new Map(PAGES.flatMap((p) => pageChecks(p, r)).map((c) => [c.id, c]));
}

export function sections(page, r) {
  const checks = pageChecks(page, r);
  return Object.entries(TIERS).flatMap(([tier, [name, subtitle, autorun]]) => {
    const members = checks.filter((c) => c.tier === tier);
    return members.length ? [{ tier, name, subtitle, autorun, checks: members }] : [];
  });
}
