import assert from "node:assert/strict";
import { test } from "node:test";

import { classify, landed } from "../site/lib/commits.js";

test("classify", () => {
  for (const [message, want] of [
    ["Bug 2075354 - Assert when creating a PBackground child, r=nika", ["landing", [2075354]]],
    ["Bug 2077022: Ignore DOM attribute changes. r=Jamie", ["landing", [2077022]]],
    ["Bug 2077977, Bug 2077976, Bug 2077975 - Map Python license names", ["landing", [2077977, 2077976, 2077975]]],
    ['Revert "Bug 2072379 - Add test; r=smaug" for causing failures', ["backout", [2072379]]],
    ['Revert "Bug 2077977, Bug 2077976 - Map names" for causing failures', ["backout", [2077977, 2077976]]],
    ["Backed out changeset 0a1b2c (bug 1234567) for bustage", ["backout", [1234567]]],
    ["No Bug - Bumping Firefox l10n changesets r=release", [null, []]],
    // Only the leading bug list is the landing; a bug mentioned later is not.
    ["Bug 111 - Follow-up to bug 222", ["landing", [111]]],
  ]) assert.deepEqual(classify(message), want, message);
});

const c = (date, message) => ({ sha: message, date, message });
const sha = (n) => n.toString(16).padStart(40, "0");
const revert = (n, date, of) => ({ sha: sha(n), date, message: `Revert "x"\n\nThis reverts commit ${sha(of)}.` });

test("a backout cancels a landing and a reland restores it", () => {
  assert.deepEqual(landed([
    c("2026-10-01T10:00:00Z", "Bug 1 - fix"),
    c("2026-10-01T11:00:00Z", "Bug 2 - fix"),
    c("2026-10-01T12:00:00Z", 'Revert "Bug 1 - fix" for bustage'),
    c("2026-10-01T13:00:00Z", 'Revert "Bug 2 - fix" for bustage'),
    c("2026-10-01T14:00:00Z", "Bug 2 - fix (reland)"),
  ]), new Map([[2, "2026-10-01T14:00:00Z"]]));
});

test("landed does not depend on input order", () => {
  assert.deepEqual(landed([c("2026-10-01T12:00:00Z", 'Revert "Bug 1 - fix"'), c("2026-10-01T10:00:00Z", "Bug 1 - fix")]), new Map());
});

test("a partial backout leaves the other parts landed", () => {
  assert.deepEqual(landed([
    { sha: sha(1), date: "2026-10-01T10:00:00Z", message: "Bug 1 - part 1: fix" },
    { sha: sha(2), date: "2026-10-01T10:00:01Z", message: "Bug 1 - part 2: add test" },
    { ...revert(3, "2026-10-01T12:00:00Z", 2), message: `Revert "Bug 1 - part 2: add test"\n\nThis reverts commit ${sha(2)}.` },
  ]), new Map([[1, "2026-10-01T10:00:00Z"]]));
});

test("a reverted revert restores the landing", () => {
  assert.deepEqual(landed([
    { sha: sha(1), date: "2026-10-01T10:00:00Z", message: "Bug 1 - fix" },
    revert(2, "2026-10-01T11:00:00Z", 1),
    revert(3, "2026-10-01T12:00:00Z", 2),
  ]), new Map([[1, "2026-10-01T10:00:00Z"]]));
});

test("a revert by sha cancels only that commit", () => {
  assert.deepEqual(landed([
    { sha: sha(1), date: "2026-10-01T10:00:00Z", message: "Bug 1 - fix" },
    revert(2, "2026-10-01T11:00:00Z", 1),
  ]), new Map());
});
