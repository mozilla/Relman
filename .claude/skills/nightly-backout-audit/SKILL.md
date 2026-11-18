---
name: nightly-backout-audit
description: Audit backouts on main over the whole Nightly cycle, from a local Firefox checkout — the deeper version of the dashboard's Nightly check "Backed out of main, never relanded, bug resolved fixed" (nightly.backed-out-resolved) — and report bugs resolved as fixed whose fix is not in main, either because nothing relanded after the backout or because one part of a multi-part bug was backed out and never came back. Use when asked things like "any bugs resolved but backed out", "audit the backouts this cycle", "was anything resolved by accident after a backout", "which fixed bugs are missing a part", "check the backed out bugs since nightly started".
---

# Backed out, but resolved as fixed

A bug resolved as fixed after its landing was backed out looks fixed on
Nightly to every other check, and to anyone deciding on an uplift, when the
fix is not in the tree. The dashboard check `nightly.backed-out-resolved`
reads GitHub over at most 14 days, and only per bug. This skill reads the
local checkout over the whole Nightly cycle, and per patch, so it also finds a
part of a bug that was backed out and never relanded.

**Read-only.** Read git and Bugzilla, report. Never reopen bugs, set flags,
comment or edit anything.

## Setup

- **Firefox checkout:** the one the release-note scripts use, resolved by
  `scripts/relnotes/trainlib.py` from `--repo`, `$RELMAN_GECKO_REPO`, or saved
  state. If none is set up, run
  `python3 scripts/relnotes/watchlist.py check-setup --repo /path/to/firefox`
  and stop if it fails. Call the path `$REPO`.
- **Fetch main once:** `git -C $REPO fetch origin main`. Everything below reads
  `origin/main`, never the working tree.

## The bug list

The rules for what counts as a landing, a backout and a reland live in the
dashboard (`dashboard/site/lib/commits.js`), never in this skill:

```sh
node dashboard/bin/backed-out.js --repo $REPO > /tmp/backed-out.json
```

It covers the current Nightly cycle; pass `--since <ISO time>` only if the
user asks for a different window. It prints:

- **`since`**, **`head`** (the `origin/main` commit read), **`commits`** and
  **`key_used`**. With `key_used: false`, security bugs are missing; say so.
- **`bugs`**: every bug resolved as fixed with a backout on main in the window
  that was not itself reverted. Each has `last_resolved`, `backed_out` (the
  latest backout), `live_landing`, and `commits`: its own landings and
  backouts over its whole history, so some may predate `since`, oldest first,
  each with `reverts`: the sha it undid, or null for a plain landing and for
  the older "Backed out changeset" form. A landing with `reverts` set is a
  revert of a backout (`Revert "Revert "…""`), and relands what that backout
  undid.

If the command fails, stop and report the error.

## For each bug

### `live_landing: false`: nothing left in main

The same bugs the dashboard check shows, over the longer window. Before
calling one a problem:

1. **Relanded under another bug?** Search main after the backout for the
   reverted subject's wording, without its `Bug N -` prefix and reviewers,
   with `<backout>` the backout's `sha` from `commits`:
   `git -C $REPO log <backout>..origin/main -i -F --grep="<words>" --format='%H %cs %s'`.
   The range starts after the backout, so the backout itself is not listed.
   A match counts only if it is a landing (its subject does not start with
   `Revert`, `Back out` or `Backed out`) and it is still in main: no later
   commit reverts it, so
   `git -C $REPO log <match>..origin/main -F --grep="This reverts commit <match>" --format=%h`
   prints nothing, and no later backout names its bug. A match that counts
   means it relanded: leave the bug out.

Everything else is reported, with the whole bug as what's missing.

### `live_landing: true`: something is in main

Most of these relanded in full. Work it out from `commits`:

1. For each backout, find the landing it undid: the commit whose sha starts
   with `reverts`. For a backout with `reverts: null`, it undid every earlier
   landing of the bug.
2. A backed-out landing is **relanded** if a landing reverts its backout, or
   a later landing has the same subject, ignoring the reviewer list (`r=…`,
   `a=…`), or the same part number (`Part 3`, `part 3:`) with a similar
   subject.
3. Every backed-out landing relanded → leave the bug out.
4. Any backed-out landing not relanded → check step 1 of the section above
   (relanded under another bug) for that landing. If not found, report the
   bug, naming the part.

## Report

Only bugs resolved as fixed with a backed-out landing that never relanded. One
table, or "None." in its place if there are none.

| Bug | Summary | Missing | Backed out | Resolved |
|---|---|---|---|---|

**Missing** is "whole bug" or the part's subject. Dates as `YYYY-MM-DD` UTC.

Then one line: `Since <since>, origin/main <head>.`, adding "No API key: security
bugs not checked." when `key_used` is false.

Nothing else: don't describe the bugs that relanded, how many were checked, or
how they were cleared.

## Truthfulness

- Every commit, date and bug comes from the script or a command above.
- If a bug's commits don't let you tell whether a part relanded, report it
  with "could not tell" in **Missing** rather than leave it out.
- If any command fails, say which and for which bugs.
