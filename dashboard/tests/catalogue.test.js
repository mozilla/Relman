import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import * as catalogue from "../site/lib/catalogue.js";
import { inSeries } from "../site/lib/crashstats.js";
import * as versions from "../site/lib/versions.js";

// Every example link in the spec, and the live versions it was written against.
const spec = JSON.parse(readFileSync(new URL("./spec_examples.json", import.meta.url)));
const { train, details, merge_day: mergeDay } = spec.fixture;
const releases = versions.build(train, details, mergeDay);
const checks = catalogue.allChecks(releases);

// A link's parameters as a multiset, so encoding and order do not matter.
const params = (url) => [...new URL(url).searchParams].map((p) => p.join("=")).sort();

test("every spec example matches the generated link", () => {
  for (const [id, url] of Object.entries(spec.links)) {
    assert.deepEqual(params(checks.get(id).query.buglistUrl()), params(url), id);
  }
});

test("every link numbers its charts contiguously", () => {
  for (const check of checks.values()) {
    const nums = check.query.linkParams().filter(([k]) => /^f\d+$/.test(k)).map(([k]) => Number(k.slice(1)));
    assert.deepEqual(nums.sort((a, b) => a - b), nums.map((_, i) => i + 1), check.id);
  }
});

test("every query sends an order", () => {
  for (const check of checks.values()) {
    assert.ok(check.query.restParams().some(([k, v]) => k === "order" && v === "changeddate"), check.id);
  }
});

test("REST params omit link-only terms", () => {
  const q = checks.get("misc.filed-today").query;
  assert.ok(!q.restParams().some(([, v]) => v.includes("%group.")));
  assert.ok(q.linkParams().some(([, v]) => v === "%group.editbugs%"));
});

test("readable text of a nested query", () => {
  assert.deepEqual(checks.get("beta.sec-pending-affected").lines(), [
    "resolution = FIXED",
    "cf_status_firefox159 anywordssubstr verified,fixed",
    "bug_group substring sec",
    "ANY OF (",
    "  cf_status_firefox158 equals ---",
    "  cf_status_firefox157 equals ---",
    "  cf_status_firefox_esr153 equals ---",
    "  cf_status_firefox_esr140 equals ---",
    "  cf_status_firefox_esr115 equals ---",
    ")",
  ]);
});

test("post-filters are listed as then: steps", () => {
  assert.deepEqual(checks.get("beta.approved-never-landed").lines().slice(-2), [
    "then: drop bugs whose approval is on an obsolete patch, or was granted in an earlier cycle",
    "then: drop bugs named by a commit on this channel's branch",
  ]);
});

test("ESR excludes products that have no ESR versions, mainline does not", () => {
  const products = (id) => checks.get(id).lines().filter((l) => l.startsWith("product notequals"));
  assert.deepEqual(products("esr115.uplift-approved-not-fixed"),
    ["product notequals Firefox for Android", "product notequals Data Platform and Tools"]);
  assert.deepEqual(products("beta.uplift-approved-not-fixed"), []);
});

test("channels", () => {
  assert.deepEqual(releases.all.map((c) => c.key), ["nightly", "beta", "release", "esr153", "esr140", "esr115"]);
  const esr115 = releases.esrs.at(-1);
  assert.equal(esr115.shipping, "115.42.0esr");
  assert.equal(esr115.statusField, "cf_status_firefox_esr115");
  assert.equal(esr115.approvalFlag, "approval-mozilla-esr115");
  assert.equal(releases.beta.approvalFlag, "approval-mozilla-beta");
  assert.equal(releases.nightly.approvalFlag, null);
  assert.equal(releases.cycleStart, "2026-09-24T16:00:00+00:00");
});

test("the extra ESR is not duplicated when the API lists it", () => {
  const r = versions.build({ ...train, esr_previous: { version: 115 } }, details, mergeDay);
  assert.deepEqual(r.esrs.map((c) => c.key), ["esr153", "esr115"]);
});

test("ESR has a tracking queue per mainline version", () => {
  const ids = new Set(catalogue.pageChecks("esr", releases).map((c) => c.id));
  for (const esr of ["esr153", "esr140", "esr115"]) {
    for (const v of [159, 158, 157]) assert.ok(ids.has(`${esr}.tracking-not-fixed-${v}`), `${esr} ${v}`);
  }
});

test("Tier 3 and Misc do not run on load", () => {
  for (const check of checks.values()) assert.equal(check.runsOnLoad, ["1", "2"].includes(check.tier), check.id);
});

test("gap detectors", () => {
  const flagged = [...checks.values()].filter((c) => c.shouldBeZero).map((c) => c.id).sort();
  const want = ["beta.sec-high-pending-uplift", ...["beta", "release", "esr153", "esr140", "esr115"]
    .flatMap((ch) => [`${ch}.uplift-approved-not-fixed`, `${ch}.approved-never-landed`])].sort();
  assert.deepEqual(flagged, want);
});

test("crash series", () => {
  for (const [shipping, kind, yes, no] of [
    ["159.0a1", "nightly", ["159.0a1"], ["158.0a1"]],
    ["158.0b4", "beta", ["158.0b1", "158.0b4"], ["157.0b9", "158.0"]],
    ["157.0", "release", ["157.0", "157.0.1"], ["156.0.1", "157.0b9", "157.0esr"]],
    ["115.42.0esr", "esr", ["115.42.0esr", "115.42.1esr"], ["115.41.0esr", "115.42.0", "140.17.0esr"]],
  ]) {
    for (const v of yes) assert.ok(inSeries(shipping, v, kind), `${v} in ${shipping}`);
    for (const v of no) assert.ok(!inSeries(shipping, v, kind), `${v} not in ${shipping}`);
  }
});
