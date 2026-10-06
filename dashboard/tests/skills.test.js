// Every skill a check names must exist, and must name that check: the skill
// gets its bugs from bin/check.js by check id, so a rename on either side
// breaks it silently.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

import * as catalogue from "../site/lib/catalogue.js";
import * as versions from "../site/lib/versions.js";

const spec = JSON.parse(readFileSync(new URL("./spec_examples.json", import.meta.url)));
const { train, details, merge_day: mergeDay } = spec.fixture;
const checks = [...catalogue.allChecks(versions.build(train, details, mergeDay)).values()];
const skillFile = (name) => new URL(`../../.claude/skills/${name}/SKILL.md`, import.meta.url);

test("every skill a check names exists and names that check", () => {
  const withSkills = checks.filter((c) => c.skills.length);
  assert.ok(withSkills.length > 0);
  for (const check of withSkills) {
    for (const { name, summary } of check.skills) {
      assert.ok(summary, `${check.id}: ${name} has no summary`);
      assert.ok(existsSync(skillFile(name)), `${check.id}: no .claude/skills/${name}/SKILL.md`);
      const text = readFileSync(skillFile(name), "utf8");
      assert.match(text, new RegExp(`^name: ${name}$`, "m"), `${name}: frontmatter name differs`);
      assert.ok(text.includes(check.id), `${name} does not mention ${check.id}`);
    }
  }
});
