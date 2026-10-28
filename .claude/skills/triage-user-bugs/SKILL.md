---
name: triage-user-bugs
description: Triage the recent community-filed Firefox bug queue and recent regressionwindow-wanted bugs. Pulls both lists, classifies each bug (duplicate, needs-info, regression, feature request, invalid, valid-new), attempts to find regressors where findable, and escalates shipped-channel functional regressions (chemspill-class). Use when the user wants to triage incoming user bugs, work the daily community bug queue, find regressors for recent regressions, or asks things like "triage the user bugs from the last day", "what came in overnight", "find the regressor for these", "anything chemspill-worthy in the queue".
---

# Triage User Bugs

Pull the recent **user-filed** bug queue plus recently-tagged **regressionwindow-wanted** bugs, triage each into an actionable bucket, find regressors where the signal allows, and surface anything that looks like a shipped functional regression that needs attention now.

**Scope: triage and report only.** This skill reads bugs and produces a triage summary. It does **not** modify bugs, post comments, set flags, change components, or set `regressed_by` — those are follow-up actions the user takes (or explicitly approves) afterward. When you've drafted a comment or a `regressed_by` attribution, present it for the user to apply; never write to Bugzilla from this skill.

## What it pulls (two sources)

**Source 1 — community-filed, last 24h.** Bugs created in the last day, in the relman product set, filed by users who are *not* automation and *not* privileged (the editbugs/mozilla-corporation exclusion — see the proxy note below). This is the incoming user queue.

**Source 2 — `regressionwindow-wanted`, last few days.** Bugs that recently got the `regressionwindow-wanted` keyword — the explicit "please find the regression window" list. Spend regressor-hunting effort here first, since these are pre-identified as needing it.

The 24h list often appears as the advanced-search link
`https://bugzilla.mozilla.org/buglist.cgi?...chfieldfrom=-24h...&v2=%25group.editbugs%25&v3=%25group.mozilla-corporation%25...`.
You can't fetch that page directly (see below) — use the REST translations in the helper script, or run `scripts/fetch-bugs.sh`.

## Acquiring the lists

There are three hard constraints; the skill is built around them.

1. **`buglist.cgi` is behind a bot-challenge WAF.** The HTML page (and `ctype=csv`) returns a JavaScript "Client Challenge" / "a required part of this site couldn't load" page through WebFetch or curl, **not** data. Do not try to scrape it. Use the **REST API** (`/rest/bug`), which is not challenged.

2. **REST can't evaluate the `%group.*%` pronouns.** The original query excludes reporters in `editbugs` and `mozilla-corporation` via `%group.editbugs%` / `%group.mozilla-corporation%`. Those pronouns are UI-only; REST returns HTTP 400 ("The group you specified ... is not valid here"). **Proxy:** filter on `bug_status=UNCONFIRMED`. Community/unprivileged reporters lack `canconfirm`, so their bugs land UNCONFIRMED, while employee/editbugs bugs come in as NEW/ASSIGNED. In practice this matches the intent closely (a recent run: 334 unfiltered → 42 UNCONFIRMED). **Always state in the output that you used the UNCONFIRMED proxy**, so the filtering is auditable.
   - *Auth-later option:* if a Bugzilla API key is available in the environment, you may instead fetch `buglist.cgi?...&ctype=csv` with the key for an exact `%group%` match. Until then, the UNCONFIRMED proxy is the default.

3. **Use the `moz` MCP for bug *detail*, REST only for *enumeration*.** REST gives you the ID list and lightweight fields; read `@moz:bugzilla://bug/{id}` (or the `get_bugzilla_bug` tool) for full description, comments, and history when judging a bug. If the `moz` MCP isn't connected this session, fall back to `curl -s https://bugzilla.mozilla.org/rest/bug/{id}` and `/rest/bug/{id}/comment` — and say you did.

`scripts/fetch-bugs.sh` performs both REST queries, dedupes (a bug can appear in both lists), and prints an id/component/version/keywords/regressed_by table plus the raw JSON paths. Run it first; it encodes the exact product set and the UNCONFIRMED proxy.

**Always invoke it without an OUTDIR argument** (e.g. `scripts/fetch-bugs.sh` or at most `scripts/fetch-bugs.sh -24h -4d`). With no OUTDIR it defaults to a fresh `mktemp -d`, so scratch JSON never lands in the repo. Do **not** pass a repo-relative path (like `artifacts/...`) and do **not** `mkdir` a scratch directory — this skill is read-only and should leave no working-tree artifacts.

## Freshness — do not analyze outdated patches

This is a first-class rule, not a footnote. Getting it wrong produces confidently-wrong diagnoses.

- **Read current code, not a snapshot or a commit message.** Prefer `searchfox-cli` (tracks mozilla-central HEAD) for any code or identifier lookup. If you use a local Gecko checkout, first confirm it's current: check `git -C <checkout> log -1` is recent and `git status` is clean (or account for local edits). **Never quote a commit's diff or commit message as if it were the current code** — patches get reworked, reverted, and re-landed (a single bug can have three different "Part 1" landings across central, beta-uplift, and a release dot-release).
- **Separate "what shipped in release X" from "what's on central now."** When a bug is about a specific shipped version, read that version's code (`git show FIREFOX_<ver>_RELEASE:path`); when it's about going-forward behavior, read central. A mechanism that exists on central may differ from or be absent in the shipped build, and vice versa.
- **Version-timing guardrail on every regressor candidate.** Before naming a regressor, verify the candidate's landing version (`cf_status_firefoxNN` / `target_milestone` on the *candidate* bug) is **≤ the version this bug was reported against / first appears in**. A change that first ships in 154 cannot regress a 152 report — reject such candidates explicitly. (This is the exact mistake to avoid: pattern-matching a plausible refactor that landed too late.)
- **Pull bug data fresh at run time.** Never reuse a cached list; record the query window in the output.

Know the current channel mapping when you run (it shifts ~monthly): release = NNN.0.x, beta = NNN+1, nightly = NNN+2, with ESR separate. Confirm the live numbers rather than assuming.

## Triage taxonomy

Put each bug in exactly one bucket:

- **DUPLICATE** — give the bug number it duplicates (search via `quicksearch`). Note if its existing `regressed_by`/links are wrong.
- **NEEDS-INFO** — name the specific missing piece (STR, last-good version, profile, testcase, OS, crash ID, fresh-profile result).
- **REGRESSION-FINDABLE** — a clear regression where a candidate regressor or a bisection range can be produced (see methodology).
- **CLEAR-FUNCTIONAL-REGRESSION** — a core function is broken even if no regressor is found yet; flag for priority.
- **FEATURE-REQUEST** — enhancement, not a defect.
- **INVALID / WORKSFORME** — not a real bug, works as intended, or self-inflicted config.
- **VALID-NEW** — legitimate, reproducible-looking new bug, ready to confirm.

Per-bug, also note: a wrong bugbug auto-component, cross-links/See-Also, and whether the bug is out of scope (e.g. a Thunderbird bug surfaced by the keyword query — note and set aside; not actionable from a Firefox checkout).

## Finding regressors

**Regressor hunting is a first-class pass, not an afterthought — prioritize it whenever the bucket qualifies.** Run regressor discovery on **every** bug that lands in **CLEAR-FUNCTIONAL-REGRESSION**, **REGRESSION-FINDABLE**, and **VALID-NEW** — not just the `regressionwindow-wanted` set. For VALID-NEW the hunt doubles as a regression check: conclude "longstanding / not a regression" with git evidence when that's what the archaeology shows — don't force a candidate, but don't skip the check either (a bug mis-triaged as "longstanding" can hide a live regression with a trivial fix). **Skip** the minor buckets: INVALID/WORKSFORME, NEEDS-INFO, FEATURE-REQUEST, and out-of-scope (NEEDS-INFO by definition lacks the signal to hunt).

**DUPLICATE is conditional — this is the easy one to get wrong.** Skip the hunt only when duping really is the *only* action left, i.e. the dup target already has a `regressed_by` (then just validate it, per step 1 below). If **neither the duplicate nor its target has a regressor**, run the full hunt on the target. Closing a bug forward without doing so buries a findable regressor, and the newer duplicate is often the better-evidenced report — a second reporter usually adds the boundary, testcase, or partial-failure detail that makes the archaeology possible in the first place.

Priority order: shipped-channel CLEAR-FUNCTIONAL-REGRESSION first, then REGRESSION-FINDABLE, then VALID-NEW, then regressor-less DUPLICATE pairs. **Do the code archaeology — do not default to recommending a mozregression range.** Only fall back to mozregression when archaeology is genuinely blocked, and then state the specific reason and the narrowest range.

Attempt discovery in this order — don't jump straight to "needs a mozregression range":

1. **If `regressed_by` is already set — validate, don't repeat.** Pull the named regressor; check version-timing (above) and area plausibility (does it touch code matching the symptom?). Correct it if it's actually a fix-tracking bug, a too-late landing, or the wrong area.

2. **If there's a clean good/bad version boundary + an identifiable subsystem — do code archaeology over the release-tag range.** This is the workhorse and needs no mozregression. Reference recipe (how the 151→152 skip-link regressor, bug 2034851, was found):
   - Locate the mechanism in current code (e.g. the "sequential focus navigation starting point" → `PresShell::GoToAnchor` / `nsFocusManager` / `Document::SetFocusNavigationStartingPoint`).
   - `git log --oneline FIREFOX_<good>_RELEASE..FIREFOX_<bad>_RELEASE -- <implicated files>` and scan for commits in the matching area.
   - Match by area + read the diff for *how* it changed the behavior; apply the version-timing guardrail.
   - State the candidate, the mechanism, and a suggested fix direction — labeled "candidate," never asserted as certain.
   - For dot-release regressions, diff the two release tags directly (e.g. `FIREFOX_152_0_1_RELEASE..FIREFOX_152_0_2_RELEASE`) — those ranges are small (tens of commits) and often pinpoint it.

3. **Otherwise — recommend a mozregression range from the reporter, and say why** code archaeology wasn't viable (vague boundary like "a few weeks ago" with no version, no reproducible STR, or no identifiable subsystem). mozregression (download-and-bisect builds against a live repro) is the fallback, not the first resort.

### Deep-dive every candidate before you name it

A candidate is a hypothesis until it survives this. **Do not report a regressor candidate without working the whole list** — a plausible-looking candidate that turns out wrong is worse than "no regressor found," because someone will set `regressed_by` on it and stop looking. Run all seven:

1. **Prove version timing by tag containment, not by arithmetic.** `git merge-base --is-ancestor <commit> FIREFOX_<ver>_RELEASE` against the version the bug was reported against, plus the tag *before* it. Do not infer the shipping version from the landing date and the merge calendar — that reasoning is wrong often enough to matter (a candidate that looks like it landed one cycle late can be in the release tag anyway). Also read `target_milestone` / `cf_status_firefoxNN` on the candidate bug itself and reconcile the two; if they disagree, containment wins and say so.

2. **Read the pre-patch state, not just the diff.** `git show <commit>^:<path>` and compare against current. The claim you need is "before this, X happened; after, Y happens" — a commit merely *touching* the implicated area is not evidence. This is the single highest-value step; it is what separates a real mechanism from area pattern-matching.

3. **Make the mechanism explain every observation, including the ones that work.** A reporter who says "A fails, B works, C works" has handed you three constraints. A candidate that explains only the failure is weak; one that explains all three is strong. If any reported-working case contradicts your mechanism, the mechanism is wrong — say so rather than filing off the corner.

4. **Mine the candidate's own `regressions` list and follow-up bugs.** If the candidate already caused a same-symptom-family regression, that is the strongest corroboration available, and it often hands you a fix that already exists. Check whether that fix is on other branches — it changes the recommendation from "needs a fix" to "needs an uplift decision," which is a different and more urgent action.

5. **Confirm the mechanism is live in the affected build.** Check any gating pref's value *in that version's tree* (`git show FIREFOX_<ver>_RELEASE:modules/libpref/init/StaticPrefList.yaml`), plus Nimbus/XPI gating where relevant. A mechanism behind a pref that is off in the shipped build cannot be the cause.

6. **Try to falsify it.** Name the check that would disprove your candidate and run it. Expect to discard hypotheses at this step — a reverted commit in a dot-release diff that merely *sounds* related, or a mechanism whose code path turns out never to be reached. Retract cleanly in the output; a corrected mechanism is a result, not a failure.

7. **State the residual gap and the one cheap test that closes it.** If you cannot verify end-to-end from code (no build, behavior only observable at runtime), say exactly what is unverified, then name the narrowest decisive test — usually two specific adjacent versions bracketing the candidate's landing (`N-1` vs `N`), or a currently-downloadable ESR that predates it. That beats a blind mozregression range and is a far better ask of a reporter.

Only after all seven: report it as `candidate`. Reserve `validated` for a candidate that already had `regressed_by` set and passed steps 1–5, or one where the mechanism is confirmed end-to-end. Never write to Bugzilla either way.

`searchfox-cli` may not be installed in every environment; if it isn't, use a current local checkout with narrow `rg` / `git log` and note the constraint.

## Escalation — what needs attention now

Flag prominently, at the top of the output, anything that looks like a **functional regression in a shipped channel**:
- multiple independent reporters, or
- "worked in X.0.(n-1)" / a clean version boundary on a core feature (startup, print, focus/keyboard input, file handling), or
- it appears in a dot-release diff, or
- **data loss** (deleting user files/profiles) — flag these even when classed NEEDS-INFO.

This is the "tell me like the last chemspill" signal (e.g. the 152.0.2 newtab/l10n startup-freeze, or a release-channel "no modals receive keyboard focus"). Distinguish a *new* escalation from one already tracked by an open bug (note the tracking bug instead).

## Execution model (adaptive)

Two passes: **(1) classify**, then **(2) hunt regressors** on the CLEAR-FUNCTIONAL-REGRESSION / REGRESSION-FINDABLE / VALID-NEW buckets, plus any DUPLICATE pair where neither bug has a regressor (see Finding regressors → prioritize the hunt). Pass 2 is not optional when qualifying bugs exist.

- **Small list (≲ 15 bugs):** classify inline one bug at a time, then hunt regressors on the qualifying buckets.
- **Larger list:** fan out parallel subagents in batches (e.g. ~7–8 bugs each) for pass 1. For pass 2, fan out one focused regressor-hunt subagent per qualifying bug (or small groups of related bugs), each instructed to do archaeology first and label any candidate "candidate." Give every subagent the freshness + version-timing guardrails **and the seven-step candidate deep dive** verbatim — a subagent that returns a candidate without the deep-dive evidence has not finished, so send it back rather than relaying an unverified candidate. Then synthesize.

When synthesizing, spot-check any candidate you intend to relay, especially one that contradicts a hypothesis already posted on the bug: re-run steps 1 and 2 yourself. Subagents report plausible-sounding candidates confidently; the containment test and the pre-patch read are cheap enough to repeat and are what catch it.

Dedupe across the two sources before assigning batches (a bug can be in both).

## Re-runs in the same session

When the user says "run again" (or similar) in a session that has already produced a triage report, re-run `scripts/fetch-bugs.sh` with fresh data, then filter it before triaging. Don't ask whether to filter.

- **Exclude every bug the user already saw.** That means any bug shown in a table this session, plus any bug cited as a dup target, related bug, or regressor. You may still cite one of these as a dup target, but don't re-triage it.
- **Exception: escalations that are still pending action.** For each earlier Escalations row, re-check its live state through REST: resolution, `regressed_by`, uplift and approval flags, `cf_status_*`, and new comments.
  - **Action taken:** if `regressed_by` was set, a fix landed, uplift was requested or approved, or the bug was resolved, drop it.
  - **Still pending:** show it again in a **Still pending from earlier** table at the top of Part 1, using the same columns as Escalations. Its Explanation gives only the **Status:** change since the last report and the outstanding **Action:**. Don't repeat the earlier evidence.
- **Bugs triaged but never shown.** These are bugs in Part 2 groups the user didn't open. Keep their earlier classification without re-triaging, and fold them into this run's Part 2 group counts.
- **State the filter in the Method note.** Give the number fetched, the number excluded as already seen, the number of pending escalations re-shown, and the number of new bugs triaged.
- **Nothing new:** if no new bugs remain and no escalation is still pending, say so in one line.

## Output

The report comes in **two parts**. Part 1 covers what needs a decision now. Part 2 is everything else, and it is shown only if the user asks for it. Most runs only need Part 1.

### Format rules (apply to every section)

- **Every section is a single table, with one row per bug.** No per-bug paragraphs, no per-bug mini-tables, and no field/value tables. They are hard to scan once the output is large.
- **Each bug appears exactly once.** Don't summarize a bug in one place and give its details somewhere else. Its deep-dive evidence, component move and needinfo draft all go with its row.
- **The Explanation cell uses bold labels** so its parts are easy to tell apart: **Issue:**, **Mechanism:** (or **Before → after:**), **Containment:**, **Corroboration:**, **Test:**, **Action:**, **Component:**, **Lead:**, **Watch:**, **Request:**. For escalations, the lead label is **Data loss:** or **Security exposure:** where that applies. Use only the labels the bug needs.
- **Keep cells short.** Use fragments, not sentences, and aim for one or two lines of text per label. Very long cells stop the terminal from rendering the table and leave raw pipes. If an Explanation is getting long, cut words; don't add rows.
- **Needinfo drafts go on a `↳ needinfo` row** directly under the bug's row. Put the italic, quoted draft in the last column and leave the other cells empty. Trim the draft to the questions that matter.
- **Mark deep-dived regressors** in the Regressor column with `(deep dive)`, for example `bug 2069901 — candidate (deep dive)`, and put the evidence in that row's Explanation as **Mechanism**, **Containment** and **Test**. Add **Corroboration** only when it changes confidence.
- **Regressor column values:** a bug number plus `candidate` or `validated`, or one of `none — longstanding`, `none — blocked (reason)`, or `n/a`. Never present a candidate as certain.
- Security-restricted bugs can be named as leads, but mark them **"security-restricted — don't cite publicly"**.

### Part 1: always shown, in this order

1. **Escalations.** Columns: `Bug # | Bucket | Regressor candidate | Explanation`. Include shipped-channel regressions, data loss and security exposure, whatever their bucket.
2. **Duplicates.** Columns: `Bug # | Duplicate of | Regressor candidate | Explanation`. If the target already has `regressed_by`, write `n/a (target has NNN; checked)`. An escalation-worthy bug that is already tracked by a fixed or open dup target goes here, with that fact noted, not under Escalations.
3. **Other regressions with a candidate.** Columns: `Bug # | Bucket | Regressor candidate | Explanation`. These are bugs not covered above that got a named regressor candidate.

Example:

| Bug # | Bucket | Regressor candidate | Explanation |
|---|---|---|---|
| 2085863 | REGRESSION-FINDABLE | bug 2069901 — candidate (deep dive) | **Issue:** Fenix AV1 WebRTC video is black (Chrome Remote Desktop); Release since 157. **Mechanism:** AV1 now goes to MediaCodec instead of dav1d. **Containment:** in 157, not 156; pref off in 156, on in 157. **Test:** Fenix 156 vs 157. **Action:** turn the pref off on Android only and uplift to 160. **Component:** → Core::WebRTC: Audio/Video. |
| 2086049 | NEEDS-INFO | n/a | **Security exposure:** passwords shown in cleartext; the field has no `type="password"`, so it went to form history. **Component:** → Toolkit::Form Manager if confirmed. |
| ↳ needinfo | | | *"The password input has no `type` attribute, so Firefox saves it to form history. Could you check the live page for `type="password"`? ..."* |

After the Part 1 tables, add a brief **Method** note as a few bullets: the exact query window, the **UNCONFIRMED-proxy disclaimer**, REST vs MCP, the freshness basis (release tags, searchfox), whether you re-checked candidate containment yourself, and anything you couldn't determine.

End Part 1 with a **prompt listing the remaining groups and their counts**. Name one or two notable bugs per group in a few words so the user can decide whether to look. For example:

> **There are 33 more bugs. Want to see any of these groups?**
> 1. **Needs-info (12):** includes 2086588 and 2086294.
> 2. **Valid-new, no regressor (14):** includes 2086560 (a reasonable 160 uplift).
> 3. **Feature requests (6).**
> 4. **Invalid (1).**

Then stop and wait for the answer.

### Part 2: shown on request

Use the same table rules. Columns: `Bug # | Regressor candidate | Explanation`. Show one table per requested group:

- **Needs-info.** Every row gets a `↳ needinfo` row, and component moves go in the bug's own row.
- **Valid-new, no regressor.** These are longstanding or blocked bugs. Keep the git evidence for "longstanding" in the bug's row as a short **Mechanism** fragment (for example, "check unchanged since 2014").
- **Feature requests.** Use **Request:** and **Action:**.
- **Invalid / worksforme.**
- **Out-of-scope.** Include this only if there are any, for example Thunderbird bugs.

Every bug from the fetch must land in exactly one Part 1 or Part 2 table, so the counts add up to the total. When Part 2 is done, say so ("That's all N bugs").

Subagents must return, per bug, a component move and a needinfo draft where applicable, so that synthesis can place them inline.

## Mozilla context cheatsheet

- **Meta/aggregate crash signatures** bucket many unrelated causes under one generic frame; the real cause is in `java_stack_trace_raw` / the `Caused by:` line, not the signature.
- **bugbug** auto-routes components and is often wrong — sanity-check and note mis-routes.
- **Train-hop add-ons** (e.g. newtab XPI) and **langpacks** (locale webextensions) gate some behavior; "works in safe/troubleshoot mode" is a useful gating signal (safe mode disables webextensions, including langpacks).
- **ESR vs release/beta/nightly** cadence matters for both version-timing and "is this even affected."
- A bug's **component/summary may name one OS** while the fix is platform-agnostic; confirm via the changed files' paths.
