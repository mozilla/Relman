---
name: triage-user-bugs
description: Triage the recent community-filed Firefox bug queue and recent regressionwindow-wanted bugs. Pulls both lists, classifies each bug (duplicate, needs-info, regression, feature request, invalid, valid-new), attempts to find regressors where findable, and escalates shipped-channel functional regressions (chemspill-class). Use when the user wants to triage incoming user bugs, work the daily community bug queue, find regressors for recent regressions, or asks things like "triage the user bugs from the last day", "what came in overnight", "find the regressor for these", "anything chemspill-worthy in the queue".
---

# Triage User Bugs

Pull the recent **user-filed** bug queue plus recently-tagged **regressionwindow-wanted** bugs, triage each into an actionable bucket, find regressors where the signal allows, and surface anything that looks like a shipped functional regression that needs attention now.

**Scope: triage and report only.** This skill reads bugs and produces a triage summary. It does **not** modify bugs, post comments, set flags, change components, or set `regressed_by` — those are follow-up actions the user takes (or explicitly approves) afterward. When you've drafted a comment or a `regressed_by` attribution, present it for the user to apply; never write to Bugzilla from this skill.

## Focus — what the report is for

The report exists to surface **what needs release-management attention in the current cycle**. Classify every bug, but **report** only these:

1. **Escalations.** Shipped-channel functional regressions, data loss, and security exposure (see Escalation below).
2. **Current-cycle regressions.** Anything that is new in, or newly affects, the current nightly, beta, or release version, with or without a regressor candidate. Hunting a regressor for these is the main work.
3. **Clear regressors no bot has investigated.** Any bug with a clear version boundary or regressor where no triage bot has posted an analysis.
4. **Duplicates that looked like a new regression.** Only when a bug first looked like a current-cycle regression but turns out to be an existing bug. Ordinary dupes of longstanding bugs are not reported.
5. **Patterns not yet identified.** Several bugs in this run, or in this run plus recent open bugs, that share a symptom, site, platform, or version boundary and aren't already tied together by a tracking bug or dup chain. Report these even when each bug alone would be needs-info or longstanding.
6. **Component moves**, offered at the end as a question rather than listed, and only for bugs currently in a **General** or **Untriaged** component (for example Firefox::General, Firefox::Untriaged, Core::General, Firefox for Android::General).

**Everything else is skipped**, and appears only as counts in a one-line tally. That includes longstanding bugs, needs-info with no current-cycle signal, ordinary dupes, feature requests, invalid bugs, and bot-handled bugs (below).

### Bot-handled bugs

Skip a bug that a triage bot has already investigated. A bot has investigated a bug when a comment contains an actual diagnosis or regressor analysis. The known accounts are:
- `hackbot@mozilla.tld`
- `firefoxmanagerdev@gmail.com`
- `clouseau-bot@mozilla.tld`
- any other account whose name marks it as a triage or analysis bot

A comment from `release-mgmt-account-bot@mozilla.tld` that only moves the component (the bugbug notice) is **not** an investigation.

**Don't skip a bot-handled bug if** it needs attention for the current cycle and something is still missing. Examples:
- the bot found a current-cycle regressor but `regressed_by` isn't set
- `cf_status_firefoxNN` or tracking flags for the current nightly, beta, or release are unset
- it's an escalation-class issue
- the bot's conclusion fails the version-timing or containment check

In that case, report only the missing piece; don't redo the bot's analysis. Validate a bot-named regressor with deep-dive steps 1–2 before relaying it.

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

**Effort follows Focus.**
- **Full seven-step deep dive:** escalations, current-cycle regressions, and clear-regressor bugs that no bot has investigated.
- **VALID-NEW:** a quick longstanding check (is the implicated code unchanged across the current release tags?) is enough to decide whether it's current-cycle. Go deeper only if it is.
- **Bot-handled bugs:** don't re-hunt. Validate a bot-named regressor with steps 1–2 only.

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
  - **Still pending:** show it again in a **Still pending from earlier** table at the top of the report, using the same columns as Escalations. Its Explanation gives only the **Status:** change since the last report and the outstanding **Action:**. Don't repeat the earlier evidence.
- **Bugs that were skipped earlier stay skipped.** Count them in the Skipped tally as "previously skipped" unless they now meet the Focus criteria (for example, a new reporter forms a pattern, or the bug is newly confirmed on the current release).
- **State the filter in the Method note.** Give the number fetched, the number excluded as already seen, the number of pending escalations re-shown, and the number of new bugs triaged.
- **Nothing new:** if no new bugs remain and no escalation is still pending, say so in one line.

## Output

Report only what Focus says to report. Use the format rules below for every table.

### Format rules

- **Every section is a single table, with one row per bug.** No per-bug paragraphs, no per-bug mini-tables, no field/value tables.
- **Each bug appears exactly once.** Its deep-dive evidence and needinfo draft go with its row. Component moves are the only exception: they are held back for the closing prompt.
- **The Explanation cell uses bold labels** to separate its parts: **Issue:**, **Mechanism:** (or **Before → after:**), **Containment:**, **Corroboration:**, **Test:**, **Action:**, **Missing:**, **Bot:**. For escalations, lead with **Data loss:** or **Security exposure:** where that applies. Use only the labels the bug needs.
- **Keep cells short.** Use fragments, aiming for one or two lines per label. Very long cells stop the table rendering in the terminal.
- **Needinfo drafts go on a `↳ needinfo` row** directly under the bug: put the italic, quoted draft in the last column and leave the other cells empty.
- **Mark deep-dived regressors with `(deep dive)`** in the Regressor column, and put the evidence in that row as **Mechanism**, **Containment** and **Test**.
- **Regressor column values:** a bug number plus `candidate` or `validated`, or `none — longstanding`, `none — blocked (reason)`, or `n/a`. Never present a candidate as certain.
- **For a bot-handled bug that is reported because something is missing,** add **Bot:** with the bot's conclusion in a few words, and **Missing:** for what still needs doing.
- **Security-restricted bugs** may be named as leads, marked **"security-restricted — don't cite publicly"**.

### Sections, in this order

Omit a section if it would be empty.

1. **Escalations** — `Bug # | Bucket | Regressor candidate | Explanation`
2. **Current-cycle regressions** — `Bug # | Affects | Regressor candidate | Explanation`. **Affects** names the channels and versions, for example "Release 159, Beta 160". This section includes clear-regressor bugs with no bot investigation, and bot-handled bugs reported for a missing piece.
3. **Looked new, actually dupes** — `Bug # | Duplicate of | Regressor candidate | Explanation`. Say why it looked new and where the dup target stands, for example "fixed in 159", or "open, regressed_by set".
4. **Possible patterns** — `Bugs | Shared signal | Explanation`. Give the suggested tracking or dup action.

Example:

| Bug # | Affects | Regressor candidate | Explanation |
|---|---|---|---|
| 2085863 | Release 159, Beta 160 (since 157) | bug 2069901 — candidate (deep dive) | **Issue:** Fenix AV1 WebRTC video is black. **Mechanism:** AV1 now goes to MediaCodec instead of dav1d. **Containment:** in 157, not 156; pref off in 156, on in 157. **Test:** Fenix 156 vs 157. **Action:** turn the pref off on Android only and uplift to 160. |
| 2086560 | Release 159 | none — longstanding | **Bot:** hackbot diagnosed it and proposed a fix. **Missing:** severity and status flags for 159/160 unset; worth a 160 uplift once the fix lands. |

After the tables, add:
- **Skipped:** a single line of counts, for example "Skipped 31: 12 needs-info, 9 longstanding, 4 bot-handled, 4 feature requests, 1 dupe, 1 invalid". Don't list them; the user can ask.
- **Method:** a few bullets covering:
  - the exact query window
  - the **UNCONFIRMED-proxy disclaimer**
  - REST vs MCP
  - the freshness basis (release tags, searchfox)
  - whether you re-checked candidate containment yourself
  - anything you couldn't determine

End the report with a **component-move prompt**, then stop and wait for an answer. Count every bug from the run that sits in a General or Untriaged component and has a better home, including skipped bugs, because a misfiled bug in a General component still needs someone to act. For example:

> **6 bugs in General/Untriaged components look misfiled. Want to see the suggested component moves?**

If the user says yes, show one table: `Bug # | From | To | Why`, where **Why** is a few words. Leave the prompt out if there are none.

Subagents must return, for each bug:
- whether a triage bot already investigated it, and what it concluded
- which current-cycle versions it affects
- the current component
- a component move and a needinfo draft, where they apply

## Mozilla context cheatsheet

- **Meta/aggregate crash signatures** bucket many unrelated causes under one generic frame; the real cause is in `java_stack_trace_raw` / the `Caused by:` line, not the signature.
- **bugbug** auto-routes components and is often wrong — sanity-check and note mis-routes.
- **Train-hop add-ons** (e.g. newtab XPI) and **langpacks** (locale webextensions) gate some behavior; "works in safe/troubleshoot mode" is a useful gating signal (safe mode disables webextensions, including langpacks).
- **ESR vs release/beta/nightly** cadence matters for both version-timing and "is this even affected."
- A bug's **component/summary may name one OS** while the fix is platform-agnostic; confirm via the changed files' paths.
