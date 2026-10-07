// Open uplift revisions: Phabricator calls and how each revision is classified.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { cache } from "../site/lib/http.js";
import * as phabricator from "../site/lib/phabricator.js";
import { UpliftRevisions } from "../site/lib/uplift_revisions.js";

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; cache.clear(); });
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

test("Phabricator calls carry the token in the body only, and refuse redirects", async () => {
  let seen;
  globalThis.fetch = async (url, init) => { seen = { url, init }; return json({ result: { userName: "me" } }); };
  await phabricator.whoami("api-secret");
  assert.equal(seen.url, "https://phabricator.services.mozilla.com/api/user.whoami");
  assert.equal(seen.init.method, "POST");
  assert.equal(seen.init.redirect, "error");
  assert.ok(!seen.url.includes("api-secret"));
  assert.equal(JSON.parse(new URLSearchParams(seen.init.body).get("params")).__conduit__.token, "api-secret");
});

test("a refused token is told apart from an outage", async () => {
  globalThis.fetch = async () => json({ error_code: "ERR-INVALID-AUTH", error_info: "API token not valid." });
  const e = await phabricator.whoami("bad").catch((x) => x);
  assert.ok(phabricator.rejected(e));
  assert.ok(!phabricator.rejected(new Error("network down")));
});

test("open revisions are read across every page", async () => {
  const pages = [
    { data: [{ id: 1, fields: { title: "a", status: { value: "accepted" }, "bugzilla.bug-id": "11", dateModified: 1754870400 } }], cursor: { after: "x" } },
    { data: [{ id: 2, fields: { title: "b", status: { value: "needs-review" }, "bugzilla.bug-id": null, dateModified: 1759795200 } }], cursor: { after: null } },
  ];
  globalThis.fetch = async (url) => (url.endsWith("diffusion.repository.search")
    ? json({ result: { data: [{ phid: "PHID-REPO-x" }] } })
    : json({ result: pages.shift() }));
  assert.deepEqual(await phabricator.openRevisions("firefox-esr140", "t"), [
    { id: 1, title: "a", status: "accepted", bug: 11, changed: "2025-08-11T00:00:00Z" },
    { id: 2, title: "b", status: "needs-review", bug: null, changed: "2025-10-07T00:00:00Z" },
  ]);
});

test("each revision gets the problem its flags show", () => {
  const src = new UpliftRevisions({ key: "esr140", approvalFlag: "approval-mozilla-esr140", statusField: "cf_status_firefox_esr140" });
  const att = (rev, ...flags) => ({ file_name: `phabricator-D${rev}-url.txt`, flags: flags.map(([name, status]) => ({ name, status })) });
  const bugs = new Map([[1, {}], [2, {}], [3, {}], [4, {}], [5, {}], [6, {}],
    [7, { cf_status_firefox_esr140: "verified" }], [8, { cf_status_firefox_esr140: "fixed" }], [9, { cf_status_firefox_esr140: "affected" }]]);
  const attachments = new Map([
    [1, [att(10)]],
    [2, [att(20, ["approval-mozilla-esr140", "-"])]],
    [3, [att(30, ["approval-mozilla-esr153", "?"])]],
    [4, [att(40, ["approval-mozilla-esr140", "?"])]],
    [5, [att(50, ["approval-mozilla-esr140", "+"])]],
    [6, [att(99)]],
    [7, [att(71, ["approval-mozilla-esr140", "+"])]],
    [8, [att(81, ["approval-mozilla-esr140", "+"])]],
    [9, [att(91, ["approval-mozilla-esr140", "+"])]],
  ]);
  const problem = (id, bug) => src.problem({ id, bug }, bugs, attachments);
  assert.equal(problem(10, 1), "no uplift request");
  assert.equal(problem(20, 2), "uplift denied");
  assert.equal(problem(30, 3), "requested for esr153 instead");
  assert.equal(problem(40, 4), null);
  assert.equal(problem(50, 5), null);
  assert.equal(problem(60, 6), "revision not attached to its bug");
  assert.equal(problem(70, null), "no bug linked");
  assert.equal(problem(80, 999), "bug hidden from your account");
  // An approved revision left open after the bug was fixed on the branch (backed out and replaced).
  assert.equal(problem(71, 7), "approved, but bug already fixed on this branch");
  assert.equal(problem(81, 8), "approved, but bug already fixed on this branch");
  assert.equal(problem(91, 9), null); // approved and waiting to land
});
