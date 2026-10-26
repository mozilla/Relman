---
name: beta-sec-affected-audit
description: Audit the dashboard's Beta check "Bugs pending affected versions" (beta.sec-pending-affected) — bugs fixed on Nightly whose Beta, Release or ESR status is still unset — and work out from the code alone, for each bug and each branch, whether that branch is affected, unaffected, or already fixed, separating what was verified from what was inferred. Use when asked things like "which branches do these bugs affect", "set the affected versions for the pending bugs", "is esr115 affected by this fix", "audit the pending affected list". Takes the dashboard's "Open buglist for N bugs" link as its argument.
---

# Bugs pending affected versions

Each bug here is fixed on Nightly whose status for Beta, Release
or an ESR is still `---`. For every bug, this skill reads the fix and the code on
each older branch, and says whether that branch has the defect. The answers feed
the status flags and uplift decisions, so what was verified must stay separate
from what was inferred.

**Read-only, and from the code only.** Never open the bugs in Bugzilla (no MCP
bug lookups, no REST calls for bug details), never set flags, comment, or edit
anything. Commit messages can say little about the change; work from the
diff.

## The bug list

The query lives in the dashboard (`dashboard/site/lib/catalogue.js`, check id
`beta.sec-pending-affected`), never in this skill. It needs a Bugzilla API key,
because these bugs are hidden from anonymous users. In order:

1. **A link was passed** (`/beta-sec-affected-audit <url>`): take the bug
   numbers from its `bug_id` parameter (comma-separated). Use the bugs from the link, even if
   `$BUGZILLA_API_KEY` is also set.
2. **`$BUGZILLA_API_KEY` is set:** run
   `node dashboard/bin/check.js beta.sec-pending-affected --json` and use the
   `id` of each entry in `bugs`. A `state` other than `ok`: stop and report the
   `message`.
3. **Neither:** stop and ask for the link:

   > I can't see this check's bugs without a Bugzilla API
   > key. On the dashboard's Beta tab, open "Bugs pending affected
   > versions" and paste the "Open buglist for N bugs" link here,
   > or set `$BUGZILLA_API_KEY` in your shell and run me again.

No bugs: report "None." and stop.

## Setup

- **The branches to check** come from the dashboard, never from memory:

  ```sh
  node --input-type=module -e 'const v=await import("./dashboard/site/lib/versions.js"); const r=await v.load(); console.log([r.beta, r.release, ...r.esrs].map(c=>`${c.key} ${c.version}`).join("\n"))'
  ```

  `beta` → `origin/beta`, `release` → `origin/release`, `esrNNN` →
  `origin/esrNNN`. Other `origin/esr*` branches in the checkout are retired;
  ignore them.
- **Firefox checkout:** the one the release-note scripts use, resolved by
  `scripts/relnotes/trainlib.py` from `--repo`, `$RELMAN_GECKO_REPO`, or saved
  state. If none is set up, run
  `python3 scripts/relnotes/watchlist.py check-setup --repo /path/to/firefox`
  and stop if it fails. Call the path `$REPO`.
- **Fetch once, first:** `git -C $REPO fetch origin main beta release <esr branches>`.
  A stale checkout reports `bad object` for a commit that landed days ago:
  never conclude a commit doesn't exist without having fetched.
- **Read branches, never the working tree.**

## For each bug

### 1. The fix

```sh
git -C $REPO log origin/main -i --grep="bug <N>" --format='%H%x00%cd%x00%s%x00%b%x01' --date=short
```

- **Confirm the bug number exactly.** `--grep="bug 205559"` also matches
  2055599. Keep a commit only if the leading `Bug N, Bug M - …` list of its
  subject contains the bug as a whole number.
- **Land / backout / reland:** drop backouts (`Backed out`, `Revert`, or
  `This reverts commit <sha>.` in the body) and what they undid; take what is
  left.
- **Stacks:** several parts can land under one bug. Note which part is the
  substantive fix and which are tests or cleanup.
- **No commit on main:** **Could not tell** for every branch ("no landing found
  on main").

### 2. The net change

Compute the diff across all the remaining landings before reading any of it:

```sh
git -C $REPO diff <first>^ <last> -- <paths the landings touch>
```

For vendored code especially, a "vendor upstream" commit and a "re-apply local
patches" commit largely cancel out; reading them one at a time attributes the
change to the wrong hunk. If unrelated commits land between `<first>` and
`<last>` in those paths, diff each landing (`git show <sha>`) and combine them
instead.

### 3. What the fix guarantees

State it in a sentence or two: the functions, identifiers and invariants it adds
or changes, and the defect it removes. Everything below checks the branches for that
hazard, not for the patch.

### 4. Each branch

Go through the branches from Setup, one at a time.

**a. Already fixed there?** Uplifts get new SHAs, so `merge-base --is-ancestor`
and `tag --contains` miss them. Grep the branch's log by bug number, with the
same exact-number and backout rules as step 1:

```sh
git -C $REPO log origin/<branch> -i --grep="bug <N>" --format='%H%x00%s'
```

A landing that survives → **fixed** (name the commit). Skip the rest for this
branch.

**b. Is the code there?**

```sh
git -C $REPO ls-tree -r --name-only origin/<branch> -- <path>
git -C $REPO grep -n '<identifier>' origin/<branch> -- <path>
```

Don't use `git show "$rev:$path"` or `git cat-file -e "$br:$path"`: they are
unreliable with shell interpolation here. A file that was moved or renamed may
still hold the same code: search for the enclosing function by name before
deciding it is absent.

**c. Is the hazard there?** Read the whole enclosing function on the branch, not
just whether an identifier is present. The main mistake to avoid: concluding
**unaffected** because a name or helper introduced by a recent refactor is
missing, when the branch has the same hazard in an older shape. Always ask
"does this branch have code with the same hazard, written differently?"

**d. When the hazard arrived.** This is the strongest evidence. Find the commit
that introduced the code the fix changes, from the pre-fix lines on main:

```sh
git -C $REPO blame -L <start>,<end> <first>^ -- <path>
git -C $REPO tag --contains <sha>
```

Its version is the smallest major `N` among `FIREFOX_NIGHTLY_<N>_END` and
`FIREFOX_<N>_…_RELEASE` tags (betas, `FIREFOX_<N>_0bX_RELEASE`, count). No such
tag: it is in the current Nightly only. If it arrived in a version later than
the branch, the branch is **unaffected**, verified. If blame lands on a commit
that only moved or reformatted the code, follow it back
(`git blame <that sha>^ …`) to where the hazard really started.

**e. Is it switched on there?** Only when the hazard is in the branch's code
(from c and d). Look for the gate in the enclosing code and its callers: a pref
read (`StaticPrefs::foo_bar_enabled()` maps to `foo.bar.enabled` in
`StaticPrefList.yaml`; in JS `Services.prefs.get*Pref("…")`,
`defineLazyPreferenceGetter`, `StaticPrefs.…`), or the pref that turns on the
whole feature (for example `dom.webgpu.enabled` for `gfx/wgpu_bindings/`). Note
too whether the hazard only runs on some platforms (a Vulkan path is Linux and
Android, a D3D path Windows, a Cocoa path macOS). Then resolve the pref on that
branch:

```sh
python3 scripts/relnotes/pref-delta.py --lookup <pref> --rev origin/<branch> --no-fetch --format json
```

Read the column for the channel the branch ships as: `beta/*` for `beta`,
`release/*` for `release` and the ESRs. Off on every platform where the hazard
runs → **disabled**. On for any of them → stays **affected** (name the
platforms if not all). A gate that is a Nimbus feature, a build flag such as
`NIGHTLY_BUILD`, or a non-empty `unresolved_define` → keep **affected** and say
the gate couldn't be resolved. Name the gate pref only from the code, and mark
**disabled** as **inferred** when the gate was found in a caller rather than
around the changed lines.

**f. `git apply --check` proves nothing about the defect.** It checks text
only, so a patch can apply cleanly to a branch missing the identifiers it calls.
If you use it to judge how hard a port is, check those identifiers exist on the
branch separately, with `git grep` as in b.

**g. The answer for the branch:**

- **affected**: the hazard is in the branch's code and switched on (from e).
  Say what a port needs (applies as-is, needs adapting to an older code shape,
  depends on an earlier refactor that isn't there).
- **disabled**: the hazard is in the branch's code but its gate is off where it
  runs (from e). Name the pref and platforms. Say what a port would need, as
  for affected, in case the gate is turned on later.
- **unaffected**: the code isn't there, or the hazard arrived after the branch
  (from d), or the branch's code is shaped so the hazard can't happen (say why).
- **fixed**: from a.
- **Could not tell**: say what you looked for, and what's needed to settle it
  (a runtime check, knowing whether a pref or build flag is on, a code owner).

Mark every answer **verified** (shown by the code: the hazard is or isn't
present, or the introducing version dates it) or **inferred** (a judgment, such
as the same hazard in a different shape).

Code under `mobile/android/` only ships in Firefox for Android, which has no
ESR: for a fix touching only that, the ESR answer is **n/a**.

## Report

One table, a row per bug, a column per branch, using the flag values:

| Bug | Fix | beta 159 | release 158 | esr153 | esr115 |
|---|---|---|---|---|---|
| 2012345 | `abc1234` (2 parts) | affected (verified) | affected (verified) | affected (inferred) | unaffected (verified) |
| 2023456 | `def5678` | disabled (verified) | disabled (verified) | unaffected (verified) | unaffected (verified) |

Then one short paragraph per bug: what the fix guarantees, when the hazard
arrived, the reasons behind any **inferred** or **Could not tell** answer,
the gate pref and platforms behind any **disabled** answer, and any port
adaptation. Put **affected** on Beta and Release first: an uplift
there is usually the most useful finding.

End with one line: how many bugs, where the list came from (link or
`bin/check.js`), and the commit read on each branch
(`git -C $REPO log -1 --format='%h %cs' origin/<branch>`).

## Environment

- `rg` isn't installed; use `grep`. `awk` isn't on the PATH inside script
  shells; count with `python3`.
- `git log --since/--until` filters on committer date, while `--date=format`
  prints the author date. Show `%cd` so the window and the output agree.

## Truthfulness

- Every commit, version and code claim comes from a command above. Nothing
  comes from Bugzilla.
- **"Could not tell" is a correct answer.** Use it rather than guessing.
- If a command fails, say which and for which bugs. Don't silently leave bugs
  out.
