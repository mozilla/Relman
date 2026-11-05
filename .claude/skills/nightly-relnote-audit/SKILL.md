---
name: nightly-relnote-audit
description: Audit the dashboard's "Release Notes Nightly+" check (nightly.relnote-nightly-plus) — bugs whose release note is Nightly-only because the change landed behind a Nightly-only pref — and report which are still Nightly-only, which are no longer Nightly-only (and the bug that changed the pref), and which could not be determined. Use before a Nightly release notes pass, or when asked things like "are the nightly+ notes still nightly only", "has any nightly+ pref been flipped", "audit the nightly release notes", "which nightly-only features rode the trains".
---

# Nightly release-note audit

A `relnote-firefox: nightly+` bug landed a change behind a pref that was on in
Nightly only. Later bugs often change that pref: turn it on for Beta and
Release, turn it off, or remove it. This skill checks every bug in the
dashboard's **Release Notes Nightly+** check against the tree as it is today.

**Read-only.** Read Bugzilla and the Firefox tree, report. Never set flags,
comment on bugs or edit anything.

## The bug list comes from the dashboard

The query lives in the dashboard (`dashboard/site/lib/catalogue.js`, check id
`nightly.relnote-nightly-plus`), never in this skill. Get the bugs with:

```sh
node dashboard/bin/check.js nightly.relnote-nightly-plus --json
```

It runs the same code the dashboard page runs. It reads the Bugzilla API key
itself, from `$BUGZILLA_API_KEY` or else `~/.config/bugzilla/api-key` (never
read, print or copy the key); the JSON's `key_used` says whether it had one. If `key_used` is false, say in
the report that bugs hidden from anonymous users (security bugs) are not
included. If `state` is not `ok`, stop and report the `message`.

## Setup

- **Firefox checkout.** Use the one the release-note scripts use:
  `scripts/relnotes/trainlib.py` resolves it from `--repo`,
  `$RELMAN_GECKO_REPO`, or saved state. If none is set up, run
  `python3 scripts/relnotes/watchlist.py check-setup --repo /path/to/firefox`
  and stop if it fails. Call the path `$REPO` below.
- **Fetch once:** `git -C $REPO fetch origin main`. Pass `--no-fetch` to every
  `pref-delta.py` call after that. Always read `origin/main`, never the working
  tree: the checkout is often on another branch.

## For each bug

### 1. Find its landings on main

```sh
git -C $REPO log origin/main -i --grep="bug <id>" --format='%H%x00%s%x00%b%x01'
```

- **Confirm the bug number exactly.** `--grep="bug 206221"` also matches
  bug 2062212. Keep a commit only if its subject's bug list, the leading
  `Bug N, Bug M - …`, contains the bug as a whole number.
- **Drop backouts and what they undid.** A revert's body says
  `This reverts commit <sha>.` Drop that sha and the revert itself. A subject
  starting `Revert` or `Backed out` is a backout, not a landing. A bug that was
  backed out and relanded has a later landing that stays.
- **No landings left?** Put the bug under **Could not tell** ("no landing on
  main found").

### 2. Find the pref each landing changed

For each remaining landing:

```sh
python3 scripts/relnotes/pref-delta.py --range <sha>^..<sha> --no-fetch --format json
```

`changed[]` lists every pref whose default that commit changed, already parsed
from `StaticPrefList.yaml`, `all.js`, `firefox.js`, `geckoview-prefs.js` and
pdf.js. **Don't read the diff yourself:** a changed `value:` line doesn't say
which pref it belongs to, and `@IS_NIGHTLY_BUILD@` or `#ifdef` defaults aren't
visible in it.

- **The gate** is the pref whose `effective_at_window_end` became Nightly-only
  (`Nightly-only`, or a platform-limited form such as
  `nightly: win, mac, linux only; beta-early: off; …`). Other prefs the same
  commit changed, such as a `force-enabled` override left off everywhere, are
  context, not the gate.
- **No pref changed by any landing:** read the commit
  (`git -C $REPO show <sha>`). If it changes a feature's per-channel
  `defaults:` in `mobile/android/fenix/app/nimbus.fml.yaml`, the gate is that
  Nimbus feature: resolve it in step 3. Otherwise the gate is something else,
  for example a build flag or `#ifdef NIGHTLY_BUILD` in code. Put the bug under
  **Could not tell** and say which, if the commit makes it clear, marked
  **inferred** unless the commit shows it outright.
- **The gate's default depends on a build define pref-delta can't resolve:** it
  prints a `WARNING … GUESS` list. Report the bug under **Could not tell** and
  name the define.

### 3. What the gate is today

The same JSON's `effective` field is the gate's default at `origin/main` now.

- Still Nightly-only, including platform-limited Nightly-only →
  **Still Nightly-only**.
- On in Beta or Release, off everywhere, or absent (removed) →
  **No longer Nightly-only**.

**A Nimbus feature gate:** resolve the tree defaults per channel with
`python3 scripts/relnotes/pref-delta.py --fml <feature> --no-fetch`. On in
nightly (and developer) only → **Still Nightly-only**; on in beta or release,
or the feature gone → **No longer Nightly-only**, found in step 4 from
`git -C $REPO log origin/main --format='%h %cs %s' -L '/^  <feature>:/,+40:mobile/android/fenix/app/nimbus.fml.yaml'`
since the landing.

**Then check live Nimbus enrollments for every gate, pref or feature.** Tree
defaults are what a build ships with; a live experiment or rollout can turn the
gate on for part of Beta or Release anyway:

```sh
python3 scripts/relnotes/nimbus-live.py --feature <feature>   # Fenix feature gates
python3 scripts/relnotes/nimbus-live.py --pref <pref>         # desktop pref gates
```

`--pref` finds enrollments through a `setPref` or `fallbackPref` mapping in `FeatureManifest.yaml`
and through branch values that name the pref (`prefFlips`). The group follows
what users get, so read both together:

- **Still Nightly-only:** the gate is on in Nightly, by the tree default or by a
  live Nightly enrollment, and off by default in Beta and Release. A gate that
  is off in the tree but on for Nightly through a rollout belongs here. Say
  "via rollout `<slug>`" in **Now**.
- **No longer Nightly-only:** on by default in Beta or Release, removed, or
  off in the tree with no live enrollment turning it on.
- An enrollment that turns the gate on for part of Beta or Release doesn't
  change the group, because the default is still off there. Report it next to
  the bug: slug, rollout or experiment, channel, population and targeting.

A Nimbus gate goes under **Could not tell** only when the feature can't be
resolved: `pref-delta.py --fml` doesn't cover its manifest (Focus), or
Experimenter can't be reached. Its default being Nimbus-controlled is not a
reason on its own.

Sometimes no landing made a pref Nightly-only, because the landing turned it
on everywhere, but that pref is Nightly-only now: a later bug pulled it back
(seen with bug 2055599). Treat that pref as the gate. The bug is **Still
Nightly-only**: find the bug that pulled it back as in step 4, and say so in
one line.

### 4. Which bug changed it

For each **No longer Nightly-only** gate:

```sh
python3 scripts/relnotes/pref-delta.py --range <landing sha>..origin/main --no-fetch --format json
```

Take the entry for the gate pref. Its `commits` and `bugs` fields name the
commit or commits that changed it since the landing. The last commit set
today's value. If there are several, list them all, oldest first.

**Don't use `git log -S<pref>`**: flipping a default in `StaticPrefList.yaml`
edits only the `value:` line, so the pref name's count doesn't change and the
commit never shows up.

## Report

Three groups, in this order. A group with no bugs reads "None."

**No longer Nightly-only**

| Bug | Pref | Now | Changed by |
|---|---|---|---|
| 2064333 Enable scoped registries in nightly | `dom.scoped-custom-element-registries.enabled` | on by default everywhere | bug 2070506 (`bf68677ac8a7`) |

**Still Nightly-only**

| Bug | Pref | Now |
|---|---|---|

A Nimbus feature gate goes in the **Pref** column as `fml:<feature>`. Under
either table, one line per bug with a live Beta or Release enrollment of its
gate (step 3), or "No live Beta or Release enrollments" if there are none.

**Could not tell**

| Bug | Why |
|---|---|

Then one line: how many bugs the check returned, the `origin/main` commit the
tree was read at (`git -C $REPO log -1 --format='%h %cs' origin/main`), and
whether an API key was used.

## Truthfulness

- Every pref name, value, commit and bug in the report comes from a command
  above. Never infer a pref from a bug summary.
- **"Could not tell" is a correct answer.** Use it rather than guessing.
- If any command fails (git, pref-delta, check.js), say which and for which
  bugs. Don't silently leave bugs out.
