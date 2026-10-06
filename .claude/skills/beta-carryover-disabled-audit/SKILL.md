---
name: beta-carryover-disabled-audit
description: Audit the dashboard's Beta check "Carry over regressions, disabled in previous version" (beta.carryover-disabled-previous) — open regressions affecting the current Beta whose previous version is marked disabled, meaning the regressing change was not enabled in Release — and report which regressors are still off for Release in the current Beta branch code, which are now enabled (and the bug that enabled them), and which could not be determined. Use when asked things like "are the disabled carry-overs still disabled", "will these regressions ship in the next release", "audit the carry over regressions", "did any regressor's pref ride the trains".
---

# Carry-over regressions disabled in the previous version

On Beta N, a carry-over regression with status N-1 `disabled` means the change
that caused it was not enabled in the N-1 Release, usually because it sat
behind a pref that was off there. This skill checks whether that is still true
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

1. **A pref the regressor changed.** For each landing:

   ```sh
   python3 scripts/relnotes/pref-delta.py --range <sha>^..<sha> --no-fetch --format json
   ```

   Every pref in `changed[]` is a candidate. This is **verified**. It holds
   even if the landing turned the pref on everywhere: a later bug may have
   pulled it back, which is what the status records (bug 2055599 did exactly
   this).
2. **An existing pref the regressor's code checks.** If it changed no pref,
   read its diff (`git -C $REPO show <sha>`) for pref reads around the changed
   code. In C++ that's `StaticPrefs::layout_css_foo_enabled()`, whose name maps
   to a `name:` in `StaticPrefList.yaml` with `.` and `-` turned into `_`;
   confirm the real name in that file at `origin/beta`. In JS it's
   `Services.prefs.get*Pref("…")`, `defineLazyPreferenceGetter(…, "…")` or
   `StaticPrefs.…`. This is **inferred**: say so.

Don't take a pref from the bug summaries or comments. If neither step finds a
pref, put the bug under **Could not tell** and say
what you looked for. A gate that is a Nimbus feature or a build flag also goes
there; say which.

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

### 5. Who enabled it (Now enabled in Release only)

```sh
python3 scripts/relnotes/pref-delta.py --range <regressor landing sha>..origin/beta --no-fetch --format json
```

The entry for the gate pref has `commits` and `bugs`: the bugs that changed it
since the regressor landed. The last one set today's value. This is slow
(minutes), so run it only for this group.

**Don't use `git log -S<pref>`:** flipping a default in `StaticPrefList.yaml`
edits only the `value:` line, so the commit doesn't show up.

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

Mark each pref **verified** (the regressor changed it) or **inferred** (the
regressor's code reads it). End with one line: how many bugs the check
returned, the `origin/beta` commit read
(`git -C $REPO log -1 --format='%h %cs' origin/beta`), and whether an API key
was used.

## Truthfulness

- Every pref, value, commit and bug in the report comes from a command above.
  A pref is named only from the tree, never from a bug's summary or comments.
- **"Could not tell" is a correct answer.** Use it rather than guessing.
- If any command fails, say which and for which bugs. Don't silently leave
  bugs out.
