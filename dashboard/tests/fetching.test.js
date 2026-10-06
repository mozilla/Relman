// Network behaviour, with fetch stubbed out.
import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import * as bugzilla from "../site/lib/bugzilla.js";
import { Cache, SourceError, cache } from "../site/lib/http.js";
import { SecOpenOnMain } from "../site/lib/sec_open_on_main.js";

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; cache.clear(); });

const json = (status, body) => new Response(JSON.stringify(body), { status });

async function errorFrom(status, body) {
  globalThis.fetch = async () => json(status, body);
  const e = await bugzilla.whoami("key").catch((x) => x);
  assert.ok(e instanceof SourceError);
  return e;
}

test("a Bugzilla error carries its code", async () => {
  const e = await errorFrom(401, { error: true, code: 306, message: "The API key you specified is invalid." });
  assert.equal(e.code, 306);
  assert.equal(e.message, "Bugzilla error 306: The API key you specified is invalid.");
});

test("a non-string message is shown", async () => {
  const e = await errorFrom(400, { error: true, code: 32000, message: { field: "bad" } });
  assert.equal(e.message, 'Bugzilla error 32000: {"field":"bad"}');
});

test("a non-JSON body is quoted", async () => {
  globalThis.fetch = async () => new Response("<html>\n  <h1>Service   Unavailable</h1></html>", { status: 503 });
  const e = await bugzilla.whoami("key").catch((x) => x);
  assert.equal(e.code, null);
  assert.equal(e.message, "Bugzilla answered HTTP 503 with no JSON body: <html> <h1>Service Unavailable</h1></html>");
});

test("Bugzilla requests send the key and never follow redirects", async () => {
  let seen;
  globalThis.fetch = async (url, init) => { seen = { url, init }; return json(200, { name: "me" }); };
  await bugzilla.whoami("k3y");
  assert.equal(seen.init.headers["X-Bugzilla-API-Key"], "k3y");
  assert.equal(seen.init.redirect, "error");
  assert.ok(seen.url.startsWith("https://bugzilla.mozilla.org/rest/whoami"));
});

test("cache: concurrent callers share one call, and errors are not cached", async () => {
  const c = new Cache();
  let calls = 0;
  const ok = async () => { calls++; return "value"; };
  assert.deepEqual(await Promise.all([c.getOrRun("k", ok), c.getOrRun("k", ok)]), ["value", "value"]);
  assert.equal(await c.getOrRun("k", ok), "value");
  assert.equal(calls, 1);

  let fails = 0;
  const boom = async () => { fails++; throw new Error("down"); };
  const results = await Promise.allSettled([c.getOrRun("e", boom), c.getOrRun("e", boom)]);
  assert.ok(results.every((r) => r.status === "rejected") && fails === 1);
  await assert.rejects(c.getOrRun("e", boom));
  assert.equal(fails, 2);
});

const ago = (days) => new Date(Date.now() - days * 86_400_000).toISOString().replace(/\.\d{3}Z$/, "Z");

test("the security check's range re-filters one fetch", async () => {
  const calls = { github: 0, bugzilla: [] };
  globalThis.fetch = async (url) => {
    const u = new URL(url);
    if (u.host === "api.github.com") {
      calls.github++;
      return json(200, [[101, 0.5], [102, 2.5], [103, 6]].map(([b, d], i) => (
        { sha: String(i), commit: { committer: { date: ago(d) }, message: `Bug ${b} - fix` } })));
    }
    calls.bugzilla.push(u.searchParams.get("id"));
    return json(200, { bugs: [{ id: 101 }, { id: 103 }] }); // 102 is resolved, or not a security bug
  };
  const run = async (days) => {
    const [bugs, extra] = await SecOpenOnMain.fetch(["id"], "key", { days });
    return [bugs.map((b) => b.id), extra.candidates];
  };
  assert.deepEqual(await run(1), [[101], 1]);
  assert.deepEqual(await run(3), [[101], 2]);
  assert.deepEqual(await run(7), [[101, 103], 3]);
  // GitHub once, and Bugzilla always about the whole window, so neither is asked again.
  assert.equal(calls.github, 1);
  assert.deepEqual(calls.bugzilla, ["101,102,103"]);
});

test("the security check's range is clamped", () => {
  for (const [given, days] of [[{}, 3], [{ days: "1" }, 1], [{ days: "9" }, 9], [{ days: "20" }, 14],
    [{ days: "0" }, 1], [{ days: "x" }, 3]]) assert.deepEqual(SecOpenOnMain.parse(given), { days }, JSON.stringify(given));
});

test("with every Bugzilla slot busy, the newest request goes first", async () => {
  const started = [];
  const release = [];
  globalThis.fetch = (url) => {
    started.push(Number(new URL(url).pathname.split("/").at(-2))); // .../bug/<id>/attachment
    return new Promise((resolve) => release.push(() => resolve(json(200, { bugs: {} }))));
  };
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const all = [1, 2, 3, 4, 5, 6].map((id) => bugzilla.attachments([id], ""));
  await tick();
  all.push(bugzilla.attachments([100], ""), bugzilla.attachments([200], "")); // 100 waits longest
  await tick();
  assert.deepEqual(started, [1, 2, 3, 4, 5, 6]);
  release.shift()(); // one slot frees up
  await tick();
  assert.equal(started.at(-1), 200);
  while (release.length) { release.shift()(); await tick(); }
  await Promise.all(all);
  assert.deepEqual(started.slice(-2), [200, 100]);
});

test("a fresh run skips the nested caches too", async () => {
  const calls = { github: 0, bugzilla: 0 };
  globalThis.fetch = async (url) => {
    if (new URL(url).host === "api.github.com") {
      calls.github++;
      return json(200, [{ sha: "a", commit: { committer: { date: ago(0.5) }, message: "Bug 101 - fix" } }]);
    }
    calls.bugzilla++;
    return json(200, { bugs: [{ id: 101 }] });
  };
  await SecOpenOnMain.fetch(["id"], "key", {});
  await SecOpenOnMain.fetch(["id"], "key", {});
  assert.deepEqual(calls, { github: 1, bugzilla: 1 }); // second run was all cache
  await SecOpenOnMain.fetch(["id"], "key", {}, true);
  assert.deepEqual(calls, { github: 2, bugzilla: 2 }); // refresh went back to both
});
