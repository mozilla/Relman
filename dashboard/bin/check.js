#!/usr/bin/env node
// Run one dashboard check from the command line, with the same code the page
// runs, so a skill (or a person) gets exactly the bugs the dashboard shows.
//
//   node dashboard/bin/check.js --list
//   node dashboard/bin/check.js nightly.relnote-nightly-plus
//   node dashboard/bin/check.js nightly.relnote-nightly-plus --json
//   node dashboard/bin/check.js beta.sec-open-on-main --opt days=7
//
// Uses $BUGZILLA_API_KEY when set; without it, hidden bugs are not returned
// and checks that need a key report needs_key. Exits 1 unless the check ran.

import * as catalogue from "../site/lib/catalogue.js";
import * as runner from "../site/lib/runner.js";
import * as versions from "../site/lib/versions.js";

const args = process.argv.slice(2);
const json = args.includes("--json");
const opts = {};
args.forEach((a, i) => {
  if (a === "--opt") {
    const [name, ...value] = (args[i + 1] ?? "").split("=");
    opts[name] = value.join("=");
  }
});
const id = args.find((a, i) => !a.startsWith("--") && args[i - 1] !== "--opt");

const releases = await versions.load();
const checks = catalogue.allChecks(releases);

if (args.includes("--list") || !id) {
  for (const c of checks.values()) console.log(`${c.id}\t${c.title}`);
  process.exit(id || args.includes("--list") ? 0 : 1);
}

const check = checks.get(id);
if (!check) {
  console.error(`no check ${id}; --list shows them all`);
  process.exit(1);
}

const key = process.env.BUGZILLA_API_KEY || "";
const result = await runner.run(check, key, opts);
const out = {
  id: check.id,
  title: check.title,
  channel: check.channel?.label ?? null,
  key_used: Boolean(key),
  lines: result.lines ?? check.lines(),
  query_url: check.queryUrl,
  ...result,
};

if (json) {
  console.log(JSON.stringify(out, null, 1));
} else {
  console.log(`${out.title}${out.channel ? ` (${out.channel})` : ""}: ${out.state}${out.count != null ? `, ${out.count} bugs` : ""}`);
  if (out.message) console.log(out.message);
  if (!key) console.log("No $BUGZILLA_API_KEY: bugs hidden from anonymous users are not included.");
  for (const b of out.bugs ?? []) {
    const flags = Object.entries(b.flags).map(([k, v]) => `${k}=${v}`).join(" ");
    console.log(`${b.id}${b.security ? " [security]" : ""}\t${b.status} ${b.resolution}\t${b.summary}\t${flags}`);
  }
}
process.exit(result.state === "ok" ? 0 : 1);
