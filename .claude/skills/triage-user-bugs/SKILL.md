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

**Regressor hunting is a first-class pass, not an afterthought — prioritize it whenever the bucket qualifies.** Run regressor discovery on **every** bug that lands in **CLEAR-FUNCTIONAL-REGRESSION**, **REGRESSION-FINDABLE**, and **VALID-NEW** — not just the `regressionwindow-wanted` set. For VALID-NEW the hunt doubles as a regression check: conclude "longstanding / not a regression" with git evidence when that's what the archaeology shows — don't force a candidate, but don't skip the check either (a bug mis-triaged as "longstanding" can hide a live regression with a trivial fix). **Skip** the minor buckets: DUPLICATE, INVALID/WORKSFORME, NEEDS-INFO, FEATURE-REQUEST, and out-of-scope (for a DUPLICATE, only validate an already-set `regressed_by`; NEEDS-INFO by definition lacks the signal to hunt). Priority order: shipped-channel CLEAR-FUNCTIONAL-REGRESSION first, then REGRESSION-FINDABLE, then VALID-NEW. **Do the code archaeology — do not default to recommending a mozregression range.** Only fall back to mozregression when archaeology is genuinely blocked, and then state the specific reason and the narrowest range.

Attempt discovery in this order — don't jump straight to "needs a mozregression range":

1. **If `regressed_by` is already set — validate, don't repeat.** Pull the named regressor; check version-timing (above) and area plausibility (does it touch code matching the symptom?). Correct it if it's actually a fix-tracking bug, a too-late landing, or the wrong area.

2. **If there's a clean good/bad version boundary + an identifiable subsystem — do code archaeology over the release-tag range.** This is the workhorse and needs no mozregression. Reference recipe (how the 151→152 skip-link regressor, bug 2034851, was found):
   - Locate the mechanism in current code (e.g. the "sequential focus navigation starting point" → `PresShell::GoToAnchor` / `nsFocusManager` / `Document::SetFocusNavigationStartingPoint`).
   - `git log --oneline FIREFOX_<good>_RELEASE..FIREFOX_<bad>_RELEASE -- <implicated files>` and scan for commits in the matching area.
   - Match by area + read the diff for *how* it changed the behavior; apply the version-timing guardrail.
   - State the candidate, the mechanism, and a suggested fix direction — labeled "candidate," never asserted as certain.
   - For dot-release regressions, diff the two release tags directly (e.g. `FIREFOX_152_0_1_RELEASE..FIREFOX_152_0_2_RELEASE`) — those ranges are small (tens of commits) and often pinpoint it.

3. **Otherwise — recommend a mozregression range from the reporter, and say why** code archaeology wasn't viable (vague boundary like "a few weeks ago" with no version, no reproducible STR, or no identifiable subsystem). mozregression (download-and-bisect builds against a live repro) is the fallback, not the first resort.

`searchfox-cli` may not be installed in every environment; if it isn't, use a current local checkout with narrow `rg` / `git log` and note the constraint.

## Escalation — what needs attention now

Flag prominently, at the top of the output, anything that looks like a **functional regression in a shipped channel**:
- multiple independent reporters, or
- "worked in X.0.(n-1)" / a clean version boundary on a core feature (startup, print, focus/keyboard input, file handling), or
- it appears in a dot-release diff, or
- **data loss** (deleting user files/profiles) — flag these even when classed NEEDS-INFO.

This is the "tell me like the last chemspill" signal (e.g. the 152.0.2 newtab/l10n startup-freeze, or a release-channel "no modals receive keyboard focus"). Distinguish a *new* escalation from one already tracked by an open bug (note the tracking bug instead).

## Execution model (adaptive)

Two passes: **(1) classify**, then **(2) hunt regressors** on the CLEAR-FUNCTIONAL-REGRESSION / REGRESSION-FINDABLE / VALID-NEW buckets (see Finding regressors → prioritize the hunt). Pass 2 is not optional when qualifying bugs exist.

- **Small list (≲ 15 bugs):** classify inline one bug at a time, then hunt regressors on the qualifying buckets.
- **Larger list:** fan out parallel subagents in batches (e.g. ~7–8 bugs each) for pass 1. For pass 2, fan out one focused regressor-hunt subagent per qualifying bug (or small groups of related bugs), each instructed to do archaeology first and label any candidate "candidate." Give every subagent the freshness + version-timing guardrails verbatim. Then synthesize.

Dedupe across the two sources before assigning batches (a bug can be in both).

## Output

Lead with escalations, then group by bucket. Per bug, one tight line:
`<id> [BUCKET] <product::component>` + one-line reason + dup#/regressor (validated? candidate?) + ESCALATE marker if applicable.

Close with a short methodology note: the exact query window, the **UNCONFIRMED-proxy disclaimer**, which lookups used the MCP vs REST, freshness basis (checkout HEAD/searchfox), and anything you could not determine (and why). Keep it scannable — this is a working queue, not an essay.

## Mozilla context cheatsheet

- **Meta/aggregate crash signatures** bucket many unrelated causes under one generic frame; the real cause is in `java_stack_trace_raw` / the `Caused by:` line, not the signature.
- **bugbug** auto-routes components and is often wrong — sanity-check and note mis-routes.
- **Train-hop add-ons** (e.g. newtab XPI) and **langpacks** (locale webextensions) gate some behavior; "works in safe/troubleshoot mode" is a useful gating signal (safe mode disables webextensions, including langpacks).
- **ESR vs release/beta/nightly** cadence matters for both version-timing and "is this even affected."
- A bug's **component/summary may name one OS** while the fix is platform-agnostic; confirm via the changed files' paths.
