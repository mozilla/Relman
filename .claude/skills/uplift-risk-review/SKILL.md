---
name: uplift-risk-review
description: Risk-review the pending uplift requests from the dashboard's "Uplift requested" check (<channel>.uplift-requested, on the Beta, Release and ESR tabs) — for each bug, read the uplift patch itself (the branch-specific Phabricator revision, which can differ from what landed on main), the uplift request form, and how the fix has done on Nightly, and rate it low, medium or high risk with a one-line reason. Use when asked things like "review the uplift requests", "how risky are the beta uplifts", "anything risky in the esr115 uplift queue", "risk check the release uplifts". Takes the channel as an optional argument, e.g. `/uplift-risk-review esr115`.
---

# Uplift risk review

For each bug with a pending uplift request on one channel, rate the risk of
taking the uplift patch: **low**, **medium** or **high**, with a very brief
reason. This informs the approval decision; it doesn't make it.

**Read-only.** Read Bugzilla, Phabricator and the Firefox tree, report. Never
set flags, comment, or approve anything.

## Which channel

The channels come from the dashboard, never from memory:

```sh
node --input-type=module -e 'const v=await import("./dashboard/site/lib/versions.js"); const r=await v.load(); console.log([r.beta, r.release, ...r.esrs].map(c=>`${c.key} ${c.version}`).join("\n"))'
```

Each line is a channel key and version, e.g. `beta 159`, `esr115 115`. If the
argument names one of the keys, use it. Otherwise ask with AskUserQuestion, one
option per channel (label like `Fx159 beta` or `ESR 115`), with each option's
bug count in its description, so an empty queue is obvious before picking it.
The count is `count` from
`node dashboard/bin/check.js <key>.uplift-requested --json`. Call the chosen
key `<channel>`.

## Gather the facts

The query lives in the dashboard (`dashboard/site/lib/catalogue.js`, check id
`<channel>.uplift-requested`, e.g. `beta.uplift-requested`), never in this
skill. One script gets the bugs from it and collects everything mechanical:

```sh
python3 scripts/uplift/uplift-facts.py <channel>
```

It fetches the Firefox checkout first (the one the release-note scripts use;
if none is set up it says how), reads the branch `origin/<channel>`, never the
working tree, and prints JSON. A failed check stops it with the dashboard's
message. Name bugs after the channel to do just those
(`uplift-facts.py esr115 2079560`), and pass `--no-fetch` on a second run.

- **`bugs` empty and no `errors`:** report "No pending uplift requests on
  <channel>." and stop.
- **`errors`:** a bug the script couldn't read. Say which and why in the
  report; don't leave it out silently.
- **`branch_head`:** the commit read, for the report's last line.
- **`repo`:** the Firefox checkout, `$REPO` in the commands below.

Each bug's record, and what to do with it:

### 1. The uplift patch: `uplift`

- **`attachments`**: the attachments flagged `approval-mozilla-<channel>?`,
  each with its Phabricator `revision` and the main revision it was made from
  (`original`). Read each revision's diff with the moz MCP
  `get_phabricator_revision`. **Don't download the raw diff**
  (`D<id>?download=true`): Phabricator answers with a login page.
  - `original` null: the flag is on the main revision itself, or the patch was
    written for the branch. Which one shows in `landings.main` (step 2).
  - Several attachments: a stack. Review it as a whole and note which part is
    the substantive one.
  - None: the risk is **Could not tell** ("no flagged attachment"). Don't
    guess which patch is meant.
- **`form`**: the latest `firefox-<channel> Uplift Approval Request`, parsed:
  the developer's risk rating and reason, test coverage, whether it's verified
  in Nightly, string changes, user impact if declined. `count` > 1 means there
  were earlier forms. Null: no form for this channel; say so.

### 2. Landings: `landings`, `pending`

- **`landings.main`**: the bug's commits on main that the branch doesn't have
  yet as ancestors. Bug numbers are matched exactly, as in `scan-window.py`.
  `backed_out` and `revert` mark backouts (they count under *regression
  history*). The last `date` is the Nightly bake time. Empty: it never landed
  on main (*bake time* high). `part` maps a commit to its uplift revision.
- **`landings.branch`**: its uplifts already on the branch. A main commit with
  `on_branch: true` is one of them, matched by subject.
- **`landings.newer`**: the patch on a channel closer to Nightly (Beta, for a
  Release or ESR request). Counts as extra bake time: note how long.
- **`pending`**: the main commits the uplift stands for, oldest first, with
  `numstat` (lines added and removed per file).

### 3. Does it match what landed on main? `pending`, `apply`

**Compare.** Count each uplift revision's `+`/`-` lines per file and set them
beside its `pending` commit's `numstat`. Same files and counts, same lines →
"same as main". Otherwise say how it differs: rebased onto older code, lines
dropped, extra changes.

**A part in `pending` with no uplift revision:** look in the comments (step 5)
for the reason before counting it against the patch. A part can be left out on
purpose (e.g. a version bump that is right for Beta but wrong for
Release). Missing with no reason given → flag it.

**`apply`**: the `pending` commits applied in order to the branch with
`git merge-tree`, standing in for the uplift patch.

- **`clean: true`, and the uplift is the same as main:** it applies.
- **`clean: false`:** `conflict_files` lists where. The uplift revision may
  already be rebased: its `Base Revision` (from the MCP) is on the branch if
  `git -C $REPO branch -r --contains <base>` lists `origin/<channel>`. Then
  check the context lines of the revision's hunks against the branch file
  (`git -C $REPO grep -n '<a context line>' origin/<channel> -- <path>`). If
  they match, it was rebased and applies; say whether only the context differs
  from main or the changes do too.
- **Applying is not compiling.** If the patch calls functions or uses
  identifiers it doesn't add, confirm they exist on the branch with
  `git -C $REPO grep -n '<identifier>' origin/<channel> -- <path>`.


### 4. What changed around it: `churn_before`, `churn_after`, `depends_on`

A patch can apply cleanly and still rely on, or have been tested on top of,
code the branch doesn't have.

**`churn_before`**: main commits to the same files, before the fix, that the
branch doesn't have. `already_uplifted` counts the ones dropped because their
bug was uplifted. Read the commits with `touches_fix_lines: true` first: they
changed the lines the fix changes. Read
each one's change (`git -C $REPO show <sha> -- <files>`) and sort it:

- **A dependency:** the fix uses something it adds or changes (a function, a
  member, a pref, a behaviour), or edits lines it wrote. The uplift then
  depends on work that wasn't uplifted: **high** on *uplift patch difference*,
  unless the uplift revision was rebased to work without it (step 3). Name the
  bug. A conflict in `apply` often points straight at one.
- **Churn nearby:** it touches the same functions but the fix doesn't need it.
  The fix was tested on Nightly on top of it, not on the branch's code shape:
  note it, and weigh it into *uplift patch difference*.
- **Unrelated:** elsewhere in the file. Say how many there were.

**`churn_after`**: later commits to the same files on main. One that fixes,
adjusts or backs out part of this one (its subject, its bug, whether it edits
the fix's lines) is a follow-up the uplift may need. Missing from the uplift →
flag it under the table, and count it under *regression history*.

**`depends_on`**: `missing_from_branch: true` is a dependency bug that landed
on main after the branch was cut and was never uplifted: treat it as a
dependency above.

### 5. The bug

The record has `severity`, `keywords`, `status_flags`, `regressed_by`, and
`regressions` with each one's status. For the discussion, read the comments:

```sh
python3 scripts/relnotes/bug-detail.py <N> --comment all
```

Use this, not the moz MCP `get_bugzilla_bug`, for comments: the MCP redacts
some (`[Content from untrusted user removed for security]`), and these have
included phab-bot's uplift form.

- **What the developers say after landing:** comments that limit what the fix
  achieves (e.g. "won't be enough" for updates to this release), or that
  explain a part left out. These go in the notes under the table.
- **Regressions:** open ones count against it.
- **What it fixes:** `severity`, `keywords` (`topcrash`, `dataloss`, `sec-*`),
  crash volume in the comments, and `regressed_by` with `status_flags`: a
  regression is new in this release if the channel's version is `affected`
  and the one before it is `unaffected`. A new feature rather than a fix is
  worth less on both. Say "feature, not a fix" under **Value**: the approval
  question is then mostly about value.

### 6. Rate it

Assess the request against each criterion, then give it one risk rating. The
criteria play three different roles, so don't just count highs.

**What raises the risk.** These say how likely the patch is to break
something, and how widely:

| Criterion | Low risk | High risk |
|---|---|---|
| Code type | CSS, strings, test-only, a pref flip that turns something off | memory management, threading, IPC, JS JIT, graphics, networking, sandbox |
| Patch size | a few lines in 1–2 files | hundreds of lines, many files, a multi-patch stack |
| Blast radius | a niche feature, one platform, a rare path | startup, every page load, every paint, all users |
| Reversibility | stateless | a profile, storage or schema migration; sync or data formats |
| Uplift patch difference | the same as what landed on main | needed a rebase, or depends on work that wasn't uplifted |
| Strings | no l10n changes | adds or changes strings |

**What lowers it.** These are the evidence and the safety net. When they're
good they offset the risks above. When they're missing, that's normal for an
uplift, not a risk in itself, except that a bad regression history counts
against the patch:

| Criterion | Low risk | High risk |
|---|---|---|
| Nightly bake time | days on Nightly (time on a newer channel counts too) | landed today, or never landed on main |
| Test coverage | adds or updates automated tests, or existing tests already cover the changed code | no test covers the changed code |
| Pref | behind a pref or Nimbus flag we can flip remotely | no way to turn it off without a dot release |
| Regression history | no backouts, nothing regressed by it | backed out before, or has open regressions |

**Value, not risk.** These say how much the fix is worth. Report them on
their own; they don't change the risk rating:

| Criterion | Worth more | Worth less |
|---|---|---|
| Severity | S1/S2, top crash, security, data loss | S3/S4, cosmetic, a feature rather than a fix |
| Regression status | fixes a regression new in this release | an old bug, there for several releases |

Where the answers come from:

- **Code type, size, blast radius, reversibility, strings:** the uplift diff
  (step 1). Strings are `.ftl`, `.properties` or `.dtd` changes. A pref flip
  is a `value:` change in `StaticPrefList.yaml`, `all.js` or `firefox.js`:
  turning something on is not low risk.
- **Pref:** a pref or Nimbus check around the changed code in the diff. Confirm
  the pref exists on the branch:
  `python3 scripts/relnotes/pref-delta.py --lookup <pref> --rev origin/<channel> --no-fetch --format json`.
- **Bake time, regression history:** step 2 (landings, backouts, time on a
  newer channel), step 4 (follow-ups) and step 5 (`regressions`).
- **Severity, regression status:** step 5.
- **Test coverage:** a patch with no new test is fine if existing tests
  already cover the code it changes. In order:
  1. Test files in the stack.
  2. Existing tests on the branch that exercise the changed code. Tests often
     live far from the code, so search the whole tree for the changed
     function, class or file name, and for the API it implements (the
     web-facing name, the tool it builds), keeping paths with `test` in them:

     ```sh
     git -C $REPO grep -l '<name>' origin/<channel> | grep -i test
     ```

     (For example, `dom/clients/manager/ClientOpenWindowUtils.cpp` has no test
     near it, but the `clients.openWindow()` tests in
     `dom/notification/test/` cover it.) Name a test or two you found. They show the
     code runs under test, not that the changed branch does: say which.
  3. The form's "Code covered by automated testing?" answer. If it says yes
     and you found no test, say "form says covered, none found" and rate it
     unknown, not high.

  Say which one the rating rests on.
- **Uplift patch difference:** steps 3 and 4.

**The rating.** Start at **low** and move up only for a concrete reason:

- **low:** no real concern, or the concerns are offset (a risky area, but
  small, tested, and a week on Nightly or Beta without trouble).
- **medium:** one real concern that isn't offset. For example: risky code
  type or wide blast radius with little bake time, a patch reworked for the
  branch, a dependency or follow-up that isn't on the branch, string changes,
  a bad regression history.
- **high:** a credible way to break things widely (startup, every page,
  user data, a migration) with weak mitigation: just landed, not on a newer
  channel, no test coverage, or reworked for the branch.

A criterion the evidence can't settle is "unknown"; it doesn't move the
rating, but say so. The developer's own rating is one more input: say when
you disagree, and why.

## Report

One table, a row per bug, highest risk first:

| Bug | Summary | Risk | Why | Value |
|---|---|---|---|---|
| 2080718 | Crash in WebProgressListener::OnStateChange | low | 6 lines, one file, rare path; same as main; only 1 day on Nightly | S3 crash, old bug |

**Why** is one very brief line: the criteria that decided the risk, the ones
that raised it first. **Value** is a few words from the value criteria. Add a line under the table
for anything an approver must know that didn't fit: a patch that doesn't apply,
one reworked for the branch, a part left out of the stack, a dependency or
follow-up that isn't on the branch, a developer comment
that limits what the fix achieves, a disagreement
with the developer's rating.

End with one line: the channel, how many bugs, and the commit read
(`branch_head`).

## Truthfulness

- Every fact in **Why** comes from the patch, the bug, or a command above.
- **"Could not tell" is a correct answer.** Use it rather than guessing, and
  say what's missing.
- If a fetch fails, say which and for which bug. Don't silently leave bugs out.
