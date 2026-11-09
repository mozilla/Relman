---
name: beta-carryover-disabled-audit
description: Audit the dashboard's Beta check "Carry over regressions, disabled in previous version" (beta.carryover-disabled-previous) — open regressions affecting the current Beta whose previous version is marked disabled, meaning the regressing change was not enabled in Release — and report which regressors are still off for Release in the current Beta branch code, which are now enabled (and the bug that enabled them), and which could not be determined. Use when asked things like "are the disabled carry-overs still disabled", "will these regressions ship in the next release", "audit the carry over regressions", "did any regressor's pref ride the trains", "is a Nimbus rollout turning on a regressor".
---

# Carry-over regressions disabled in the previous version

On Beta N, a carry-over regression with status N-1 `disabled` means the change
that caused it was not enabled in the N-1 Release, usually because it sat
behind a pref or Nimbus feature that was off there. This skill checks whether that is still true
of the code on the Beta branch now, so whether the regression will reach
Release users when N ships.

**Read-only.** Read Bugzilla and the Firefox tree, report. Never set flags,
comment on bugs or edit anything.

## The bug list comes from the dashboard

The query lives in the dashboard (`dashboard/site/lib/catalogue.js`, check id
`beta.carryover-disabled-previous`), never in this skill:

```sh
node dashboard/bin/check.js beta.carryover-disabled-previous --json
```

- **`channel`** says which Beta it is, for example `Fx158 beta`. Each bug's
  `flags` hold the Beta and previous-version statuses, and `regressed_by` as
  comma-separated bug numbers, or `---` when none is recorded.
- **`key_used: false`** means bugs hidden from anonymous users (security bugs)
  are not included; say so in the report.
- **`state` other than `ok`:** stop and report the `message`.
- **`count` of 0:** report "None." under each group and stop. This is common.

## Setup

- **Firefox checkout:** the one the release-note scripts use, resolved by
  `scripts/relnotes/trainlib.py` from `--repo`, `$RELMAN_GECKO_REPO`, or saved
  state. If none is set up, run
  `python3 scripts/relnotes/watchlist.py check-setup --repo /path/to/firefox`
  and stop if it fails. Call the path `$REPO`.
- **Fetch both branches once:** `git -C $REPO fetch origin main beta`. Pass
  `--no-fetch` to every `pref-delta.py` call after that.
- **The question is about `origin/beta`.** Never read the working tree, and
  don't judge from `origin/main`: main is a version ahead and can differ.

## For each bug

### 1. The regressor

- **The regressor** is the bug in `regressed_by`. If it says `---`, put the
  bug under **Could not tell** ("no regressor recorded").
- **Several regressors:** do each. The bug is **Now enabled in Release** if any
  regressor is.

### 2. Its landings

```sh
git -C $REPO log origin/beta -i --grep="bug <regressor>" --format='%H%x00%s%x00%b%x01'
```

Use `origin/beta`: the regressor must be in the Beta code to matter, and this
also finds an uplift that landed on beta directly.

- **Confirm the bug number exactly.** `--grep="bug 205559"` also matches
  2055599. Keep a commit only if the leading `Bug N, Bug M - …` list of its
  subject contains the regressor as a whole number.
- **Drop backouts and what they undid:** `This reverts commit <sha>.` in a
  revert's body names the commit it undid; drop both. Subjects starting
  `Revert` or `Backed out` are backouts.
- **No landing left?** Put the bug under **Could not tell** ("regressor not in
  the Beta code").

### 3. The gate

Find what keeps the regressor's change off for Release. Use only what the
code shows, in this order:

1. **A pref or Nimbus feature the regressor changed.** For each landing:

   ```sh
   python3 scripts/relnotes/pref-delta.py --range <sha>^..<sha> --no-fetch --format json
   ```

   Every pref in `changed[]` is a candidate, and so is every feature in
   `fml_changed.features[]` (a Fenix feature added or changed in
   `mobile/android/fenix/app/nimbus.fml.yaml`). Both are **verified**. This
   holds even if the landing turned the gate on everywhere: a later bug may have
   pulled it back, which is what the status records (bug 2055599 did exactly
   this).
2. **An existing pref or Nimbus feature the regressor's code checks.** If it
   changed neither, read its diff (`git -C $REPO show <sha>`) for reads around
   the changed code. This is **inferred**: say so.
   - **Prefs.** In C++, `StaticPrefs::layout_css_foo_enabled()`, whose name maps
     to a `name:` in `StaticPrefList.yaml` with `.` and `-` turned into `_`;
     confirm the real name in that file at `origin/beta`. In JS,
     `Services.prefs.get*Pref("…")`, `defineLazyPreferenceGetter(…, "…")` or
     `StaticPrefs.…`.
   - **Fenix Nimbus features.** `FxNimbus.features.tabReloadCover` in Kotlin is
     the feature `tab-reload-cover` in `nimbus.fml.yaml` (camelCase to
     kebab-case); confirm the name in that file at `origin/beta`. A `Settings`
     property such as `settings.enableHomepageAsNewTab` counts when its
     `booleanPreference(…)` in `Settings.kt` takes its `default` from
     `FxNimbus.features.<x>`: the gate is that feature (bug 2072356 is gated
     this way).
   - **Desktop Nimbus features.** `NimbusFeatures.<id>.getVariable(…)` in JS or
     `NimbusFeatures::GetBool("<id>", …)` in C++. If the variable has a
     `setPref` or `fallbackPref` in `toolkit/components/nimbus/FeatureManifest.yaml`
     at `origin/beta`, the gate is that pref. If not, pref-delta can't resolve
     the feature's default: the bug goes under **Could not tell**, but still
     run the live check in step 4.

Don't take a pref or feature from the bug summaries or comments. If neither
step finds one, put the bug under **Could not tell** and say what you looked
for. A build flag goes there too; say which.

### 4. The gate on the Beta branch now

```sh
python3 scripts/relnotes/pref-delta.py --lookup <pref> --rev origin/beta --no-fetch --format json
```

Judge from `values` for `release/*`: that is what the Beta code becomes when it
ships. (pref-delta also reports `beta-late/*`. That's its own label, not a
Firefox channel: Beta after `EARLY_BETA_OR_EARLIER` turns off, with the same
build defines as `release`, so it never gives a different answer. Ignore it.)

- Off (`false`) or absent on every `release/*` platform →
  **Still not enabled in Release**. If it's on in `beta-early/*` (Beta in the
  first half of the cycle), add "on in early Beta only": Beta users see it
  for part of the cycle.
- On for any `release/*` platform → **Now enabled in Release**. Name the
  platforms if it's not all of them.
- `found: false` → the pref was removed from the Beta code. **Could not
  tell** ("gate pref removed"): the code it guarded may now always run, and a
  pref lookup can't show that.
- A non-empty `unresolved_define` → **Could not tell**; name the define.

**A Fenix Nimbus feature gate:**

```sh
python3 scripts/relnotes/pref-delta.py --fml <feature> --rev origin/beta --no-fetch
```

Judge from the `release/<variable>` lines, for the variable the regressor's
code reads (usually `enabled`). Off in `release` → **Still not enabled in
Release**; on → **Now enabled in Release**; the feature missing from
`nimbus.fml.yaml` at `origin/beta` → **Could not tell** ("gate feature
removed"). A feature pref-delta can't resolve (Focus, iOS) → **Could not
tell**, but still run the live check below.

**Then check live Nimbus enrollments for every gate, pref or feature.** The
tree default is what the build ships with, but a live experiment or rollout can
turn the gate on for Release users anyway:

```sh
python3 scripts/relnotes/nimbus-live.py --feature <feature> --rev origin/beta                      # Fenix
python3 scripts/relnotes/nimbus-live.py --feature <feature> --app firefox-desktop --rev origin/beta  # desktop feature
python3 scripts/relnotes/nimbus-live.py --pref <pref> --rev origin/beta                            # desktop pref
```

`--pref` finds enrollments through a `setPref` or `fallbackPref` mapping in
`FeatureManifest.yaml` and through branch values that name the pref
(`prefFlips`). Each enrollment prints its slug, rollout or experiment,
`channel`, population, targeting and branch values. Read them against the
default:

- **Off by default, but a live enrollment on `release` (or `any`) has a branch
  value that turns the gate on** → **Now enabled in Release**, with "via
  `<slug>`" and its population in **Release now**. Those users get the
  regression when the release ships, as long as the targeting doesn't exclude
  the new version. If the targeting carries a version cap below N, keep the
  bug in **Still not enabled in Release** and report the enrollment beside it.
- **A live enrollment only on `beta` or `nightly`** doesn't change the group.
  Report it beside the bug.
- **On by default, and a live `release` enrollment turns it off** → still
  **Now enabled in Release** (the default ships), with the enrollment beside it.
- **Experimenter unreachable** → keep the group from the default and say the
  live check failed.

A gate being Nimbus-controlled is not on its own a reason for **Could not
tell**: only an unresolvable feature, or a failed command, is.

### 5. Who enabled it (Now enabled in Release only)

```sh
python3 scripts/relnotes/pref-delta.py --range <regressor landing sha>..origin/beta --no-fetch --format json
```

The entry for the gate pref has `commits` and `bugs`: the bugs that changed it
since the regressor landed. The last one set today's value. This is slow
(minutes), so run it only for this group.

**Don't use `git log -S<pref>`:** flipping a default in `StaticPrefList.yaml`
edits only the `value:` line, so the commit doesn't show up.

**A Fenix Nimbus feature** gets no `--range` entry. Read the feature's history
since the regressor landed instead:

```sh
git -C $REPO log <regressor landing sha>..origin/beta --format='%h %cs %s' -L '/^  <feature>:/,+40:mobile/android/fenix/app/nimbus.fml.yaml'
```

`+40` can run past the end of the feature into the next one, so the list
includes commits that only touched a neighbouring feature. Read the diff each
commit prints and keep only those that change this feature's lines. The last
commit that changed the variable's `default` or a `defaults:`
`channel: release` value set today's value. **When a live enrollment is what
enables it,** put the slug in **Enabled by** instead.

## Report

Three groups, in this order. A group with no bugs reads "None."

**Now enabled in Release** (the regression will ship unless something changes)

| Bug | Regressor | Pref | Release now | Enabled by |
|---|---|---|---|---|

**Still not enabled in Release**

| Bug | Regressor | Pref | Release now |
|---|---|---|---|

**Could not tell**

| Bug | Regressor | Why |
|---|---|---|

A Nimbus feature gate goes in the **Pref** column as `fml:<feature>` (desktop:
`nimbus:<feature>`). Mark each gate **verified** (the regressor changed it) or
**inferred** (the regressor's code reads it).

Under the tables, one line per bug whose gate has a live enrollment on any
channel (slug, rollout or experiment, channel, population), or "No live
enrollments" if none do.

End with one line: how many bugs the check
returned, the `origin/beta` commit read
(`git -C $REPO log -1 --format='%h %cs' origin/beta`), and whether an API key
was used.

## Truthfulness

- Every pref, value, commit and bug in the report comes from a command above.
  A pref is named only from the tree, never from a bug's summary or comments.
- **"Could not tell" is a correct answer.** Use it rather than guessing.
- If any command fails, say which and for which bugs. Don't silently leave
  bugs out.
