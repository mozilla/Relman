---
name: find-release-note-candidates
description: Scan what landed on firefox-main over a window (a day, a week, or a whole Nightly cycle) and surface the changes worth asking a developer about for a release note — with bug numbers, whether the change is gated and how, feature rollups across many bugs, and a draft one-liner. Use when the user wants to find release-note candidates, work the daily "what landed overnight" pass, review a full cycle before a merge, or asks things like "what should we note from the last day", "anything user-facing land this week", "find pref flips". Also works the relnote-flag nomination queue — bugs someone flagged `relnote-firefox?` that are waiting on a decision — and answers which bugs carry a given flag value ("what's in the nomination queue", "anything waiting on a relnote decision", "what has been declined for notes"). Also use when picking release-note work back up in a fresh or just-cleared session — "let's keep looking for release notes", "continue the release note pass", "where did we get to". Complements review-release-notes (which critiques an existing draft); this one produces the candidate list from scratch.
---

# Find Release Note Candidates

Go through what landed on `firefox-main` in a window and surface the changes worth a release note.

**What this is for.** The output is a **guide for prompting developers in bugs** about whether their
change deserves a note. It is not a publication-ready draft. That shapes every judgment call below:

- **Err toward including.** A surplus candidate costs one question in a bug. A missed one ships a
  release with a note nobody wrote. When genuinely unsure, include it in a lower tier and say why
  you're unsure — don't drop it.
- **The 1.1% publication rate in the survey is not a pruning budget.** It is what survived *after*
  developers were asked and Release Management decided. Discovery sits upstream of both.

**Scope: propose only.** Read the tree and Bugzilla, produce a candidate list. Do **not** set the
`relnote-firefox` flag, comment on bugs, nominate, or publish. When you've drafted a one-liner or a
gating call, present it for the user to apply. Never write to Bugzilla or any release-notes system.

## Truthfulness is the hard requirement

This output gets used to go ask developers questions. A confident wrong claim wastes their time and
damages trust, so **never guess and never present inference as verification**. Concretely:

- **Label every claim's basis.** Mark each candidate's gating and impact statements as
  **verified** (you read the patch, the pref default, or the manifest) or **inferred** (from
  component, subject, or bug summary alone). Two different words, used consistently.
- **Say what you could not determine, per candidate** — not only in a closing note. "Could not tell
  whether this is user-visible without reading the patch" is a useful, honest line. A guess dressed
  as a finding is not.
- **Never invent** a bug number, pref name, Nimbus feature id, version, or note wording. If you
  didn't read it, don't state it.
- **Distinguish "no evidence" from "evidence of no".** "No pref gate found" and "confirmed on by
  default everywhere" are different claims. Only make the second one after reading the default.
- **Report tool failures rather than routing around them silently.** If the MCP wasn't connected,
  a bug was security-restricted, or a script capped its input, say so where it affects a
  conclusion.

## References — read these, don't restate them

**Paths below are relative to the repository root, not this skill's directory.** From the repo root,
`reference/release-notes/style-guide.md` resolves; `.claude/skills/…/reference/…` does not exist.

| Topic | File |
|---|---|
| Style rules, tags, tone, wording traps | `reference/release-notes/style-guide.md` |
| What actually clears the bar (empirical, 2 years) | `reference/release-notes/shipped-notes-survey.md` |
| REST vs MCP, backouts, freshness, channel mapping | `reference/release-notes/bugzilla-access.md` |
| Pref/Nimbus/Labs/platform gating recipes | `reference/release-notes/gating.md` |
| Command forms that don't trigger permission prompts | `reference/release-notes/command-forms.md` |
| What real passes got wrong — read before tiering | `reference/release-notes/calibration.md` |

The survey is the calibration source. Read at least its "bar in one line", the `Fixed` threshold
section, and the zero-yield table before judging significance. Note that the `Fixed`-in-majors bar
is **actively moving** (0.54 → 1.46 → 2.14 substantive notes per release over two years), so treat
"fixes don't get mainline notes" as outdated.

## Scope: you are working one release's notes

**Every Firefox release has its own release owner, and you evaluate notes only for the release you
own** — normally the current Nightly. A daily pass over Nightly N is producing candidates for N.

That changes how uplifts are reported. A bug uplifted to N-1 or N-2 belongs to *that* release
owner's queue, not yours. Surface it briefly so nothing is lost, but do not put it in the tiered
candidate list or write asks for it — say plainly that it's another owner's call.

The convention is that uplifts get flagged for notes **at uplift time** by the owner doing the
uplift. In practice that happens reliably for **dot-release** uplifts and less reliably for **beta**
uplifts, which is a known weak spot this skill can help with — see the beta-uplift mode below.

**A future mode worth knowing about:** running this over the beta cycle's uplifts to prompt that
release's owner. Same machinery, different window (`--range FIREFOX_BETA_{N}_BASE..` with
`--first-parent`), and the audience is the beta owner rather than you.

## Two modes — pick by window size and be honest about which

| | **Daily** (≤ ~2 days) | **Cycle** (a week to a full cycle) |
|---|---|---|
| Commits | ~30–530 | 2,000–7,000 |
| Survivors after the funnel | ~10–130 | ~500–2,000 |
| Approach | **Exhaustive** — every survivor gets looked at | **Signal-driven** — start from high-precision signals |
| Completeness claim | "Every landing in the window was considered." | "Bugs reached by these signals were considered; the rest were not." |

**Never claim cycle-mode coverage you don't have.** 2,000 survivors cannot each be examined. State
plainly which signals you ran, and that a change touching none of them would have been missed.

**A "day" is not a fixed size.** Quiet days run 10–20 survivors; the days after a merge run 100–130.
Measured: 07-26 gave 10 survivors, 07-21 gave 127 from 423 commits. **Above roughly 60 survivors,
walking every one is real work and the temptation is to read the multi-commit head of the list plus
the impact-evidence section and stop.** That is not exhaustive, and claiming it is, is a false
statement about coverage. Either walk the whole list or say exactly what you prioritised and what
you did not read.

**The weak-impact-evidence list is a reading-order hint, not a skip list.** It routinely holds half
the survivors — 61 of 127 on 07-21 — and real candidates live in it: bugs 2042999 (CSS `line-clamp`)
and 2050397 (passport management page) were both missed there, both internally reported with no
duplicates. Low prior, not zero.

## Choosing the window — never by date

**Commit dates on `firefox-main` are non-monotonic, so a date-based boundary is broken.** A commit
merged from autoland keeps its original committer date, so `git rev-list --before=<date>` can land
arbitrarily deep in the ancestry. Measured: `--since "24 hours ago"` picked a commit dated 20:06 that
sat **41 commits later in ancestry** than a build boundary dated 18:32, and covered only **29 of that
build's 57 bugs**. A build's commits can span **ten days** of commit dates. The date-based `--since`
flag has been removed rather than left as a footgun.

Use one of these instead:

| Purpose | Flag |
|---|---|
| Daily pass, resuming where you left off | `--since-last` (stored watermark) |
| Exactly one nightly build | `--build <id>` |
| From a chosen build up to now | `--from-build <id>` |
| Up to a chosen build, for reproducing a past window | `--to-build <id>` |
| A whole cumulative cycle | `--cycle N` |

### The daily pass — show state, then ask

**Always run `--show-state` first and let the user choose.** It prints the stored watermark, how far
behind it is, whether it belongs to an older train, and the ten most recent nightly builds with their
git commits.

```
python3 scripts/relnotes/scan-window.py --show-state
```

The watermark is per-user, outside the repo (`$XDG_STATE_HOME/relman-relnotes/watermark.json`),
because several people on the team use this and a scan must not dirty a shared working tree.

Then ask which start point they want, and say why it matters:

- **Watermark within the current train** → `--since-last` is the right resume point.
- **Watermark predates the current cycle** → it belongs to an **earlier release train**; resuming
  would sweep in a whole shipped cycle's commits. The script refuses unless `--allow-stale` is
  passed. Offer a recent build instead — this is the common case for someone returning after a break.
- **No watermark (first run)** → offer a recent build or the current cycle.

Add `--save-state` to record the window end once the run succeeds, so the next day resumes cleanly
with no gap and no double-reporting.

**One day per turn. Report it, then stop and wait for verdicts.** When the user asks for several
days, do not scan the next one — not even in the background — until they have worked through the
first. Two reasons beyond not talking over them:

- **Their verdicts are calibration input for the days that follow.** A decline reasoned "very likely
  to be uplifted per the comments" or "no duplicates in five years, so it's minor" changes how the
  next day's list should be judged. Scanning ahead spends that judgment before it arrives.
- **An interrupt mid-pass strands verification silently.** On 2026-08-04 a pass ran ahead through
  three days; the interrupt that stopped it also killed a queued precedent search, and one candidate
  went to the user with its precedent unchecked and no flag saying so.

Record each day as you finish it (`days <YYYYMMDD>`, `--save-state`, a `log` entry) so stopping
costs nothing and the next turn resumes exactly where the review did.

### Backfilling old windows vs. the normal forward pass

Normal use is **forward, one day at a time**, where the tree state and the window state coincide.
Working *backwards* through past days — as during calibration — introduces a hazard that never
arises going forward: preference state can have changed between the window and today.
`pref-delta.py` handles this by reporting `at window end:` alongside `effective now:` and flagging
when they differ, but the wider point holds for anything read from `origin/main`. When backfilling,
treat "what is true today" and "what was true then" as separate questions.

### End-of-cycle rollup check

Long-running clusters that were deferred all cycle need one deliberate pass before the merge:
**run `--cycle N` near the end of the Nightly cycle and revisit every cluster that was on hold**, to
decide whether the finished body of work now deserves a single rollup note. Interop work, multi-bug
feature pushes and preference-gated features that flipped late are the usual candidates. Track
deferrals in the watchlist (`--status watching`) so they resurface rather than being rediscovered.

### Cumulative passes

Use `--cycle N` for the wider sweeps where notes have no daily granularity — feature rollups that
only become visible across weeks, and preference flips that make earlier work live. Run
`bug-tree.py` and `pref-delta.py` over the same range.

### The nomination queue — bugs someone else proposed

Discovery works forward from what landed, but developers and triagers also nominate bugs directly by
setting `cf_tracking_firefox_relnote` to `?`. Those never appear in a window scan unless they happen
to land in it.

```
python3 scripts/relnotes/relnote-flag.py --nominated
```

**Defaults to nominations on bugs that are actually fixed, and that default matters.** Most `?` bugs
are open — a developer pre-registering an intention months ahead — and the team treats those as noise
rather than decisions waiting to be made. At the time of writing, six bugs carried `?` and only one
was fixed. `--include-open` shows the rest if you specifically want the pipeline view.

Work a fixed nomination exactly like a candidate you found yourself: the bar, the tiering, the
precedent search and the gating checks below all apply unchanged. The only difference is that someone
has already argued it deserves a note, so the question is whether you agree, and the answer is a
comment in the bug rather than a proposal in a report.

## Cycle tags

**The repository's cycle tags are the authoritative boundaries.** Don't infer a cycle from release
tags, dates, or `version.txt`; the tags exist for exactly this:

**Nightly cycle for version N — use this, it is validated:**

```
FIREFOX_NIGHTLY_{N-1}_END..FIREFOX_NIGHTLY_{N}_END
```

Note there is **no `FIREFOX_NIGHTLY_{N}_BASE` tag** — only `_END` exists, so the cycle start is the
previous version's `_END`. `FIREFOX_BETA_{N}_BASE` is the same commit as `FIREFOX_NIGHTLY_{N}_END`
(verified identical SHA), so either works as the closing boundary.

**Beta cycle for version N — work also lands during beta, so this is required for version coverage:**

```
scan-window.py --version N --first-parent \
    --range FIREFOX_BETA_{N}_BASE..FIREFOX_RELEASE_{N}_BASE
```

Three things about this range, each verified against the 153 cycle:

- **`--first-parent` is mandatory.** It restricts the walk to the beta branch's own chain. Without
  it the range pulls in every merged `main` ancestor: **71,678 commits instead of 682.**
- **The closing boundary is `FIREFOX_RELEASE_{N}_BASE`, not `FIREFOX_BETA_{N}_END`.** In the git
  mirror `FIREFOX_BETA_153_END` points at a merge-day config commit dated the *same day* as
  `_BASE` ("No Bug - Update configs after merge day operations") — it sits at the **start** of the
  beta cycle, not the end. This differs from the hg tag of the same name; don't assume the mirror's
  tags match hg's.
- **It matches the hg pushlog.** Cross-checked against
  `hg-edge.mozilla.org/releases/mozilla-beta/json-pushes?fromchange=FIREFOX_BETA_153_BASE&tochange=FIREFOX_BETA_153_END&full=1&version=2`
  (660 pushes, 820 changesets, 454 distinct bugs): the git range yields **458 distinct bugs and
  contains all 454 of hg's**, with 4 extra. Superset, so it errs toward inclusion.

Uplift commits carry an `a=<approver>` marker (`Bug 2033733 - enable LNA for all desktop users by
default. a=pascalc`). Both 153 uplifts that earned notes were **preference flips**, so run
`pref-delta.py` across the beta endpoints too, not just the nightly ones.

## Measured coverage

The Firefox 153 backtest — what a pass would have caught against the 27 bugs that earned a
published note — is in `reference/release-notes/calibration.md`.

## Invoking the scripts

**Copy the invocation form in the examples below exactly** — `python3 scripts/relnotes/<script>.py`
from the repository root, no `cd`, scratch files under `/tmp` by absolute path, no shell `for` loops,
and gecko reads through the absolute clone path from `check-setup`. Written any other way, most of
these commands stop for a permission prompt on every call.

`reference/release-notes/command-forms.md` is the single home for those rules and the measurements
behind them; it is not restated here.

## First run on a machine: locate the Gecko clone

```
python3 scripts/relnotes/watchlist.py check-setup
```

Every script needs a Gecko checkout, and **it lives somewhere different on every machine**, so no
path can be committed to the shared tree or assumed here. `check-setup` resolves it once
(`--repo` → `$RELMAN_GECKO_REPO` → saved state → `~/repos/firefox` as a legacy guess), saves it to
the per-user state file next to the watermark, and every script reads it from there.

It also reports the `Bash(git -C <clone> …:*)` permission entries needed for the gecko reads. Without
them **every single gecko read prompts** — one pass ran `git grep` eleven times and `git show` six,
all prompting, which is most of what makes a cold run feel like an approval treadmill.

- **On a machine that has never been set up, every script exits with
  `error: could not locate the Gecko checkout` and names this command.** That error is the signal —
  don't work around it. Ask where their clone is and run `check-setup --repo <path>` once.
- If it reports a path found only as the "legacy default", it is an unsaved guess: save it with
  `check-setup --repo <path>` so the other scripts stop re-guessing.
- If it reports missing permission entries, **show them and ask before writing.** `--write` merges
  them into the git-ignored `.claude/settings.local.json`; it is granting the session standing
  approval, so it is the user's call, not yours. Never add them to the shared `settings.json`.
- Use the resolved absolute path in every `git -C` call you write by hand. Don't use `~` and don't
  use `cd <clone> && git …` — permission matching is a literal prefix, so both forms miss the
  allowlist and prompt.

## Then reconstitute where you are

```
python3 scripts/relnotes/watchlist.py resume
```

**Run this before any scanning on any pass you did not personally start** (only `check-updates`
comes earlier — see below). Release-note work spans a
six-week cycle, and the session doing it will be compacted — probably more than once — and may be
replaced entirely. Nothing in a conversation survives that; the per-user state on disk does. `resume`
prints the scan position and the exact command to continue, days reviewed, status counts, what is
awaiting a reply, what a developer has already answered, what is being held for the cycle-end rollup,
and dated release-level context.

Two habits make that briefing worth reading, and without them it decays into a stale bookmark:

- **`--save-state` on every pass**, so the watermark tracks reality rather than the last time someone
  remembered.
- **Write a `log` entry at the end of every pass** — what the window was, how many candidates, what
  you rescued or declined and why. Without it the state shows *where* the scan reached but not what
  it concluded, and the next session (or the same one after compaction) cannot tell a quiet day from
  an unfinished one.
- **Record as you go.** `watchlist.py add <bug> --status asked --note "..."` when you ask,
  `days <YYYYMMDD>` when a day is done, `replied`/`decline`/`noted` as things move (only for entries
  already tracked — see the watchlist block below), and
  `log "<text>"` for context belonging to no single bug — a notes review, a decision, an open
  question. A declined bug with no recorded reason will simply be re-proposed next cycle.

### The TOOLING line — act on it before reading the rest

`resume` opens with the revision of this tooling you are running and whether it is behind
`origin/main`. Several people edit these skills and scripts, so the copy driving your pass can be
days older than the one its author is describing, and nothing else in a run would say so.

**Open every pass with `check-updates --pull`**, before `resume`. It fast-forwards this checkout
when it is behind — refusing if the tree is dirty, on a branch, or cannot fast-forward, and saying
which — so the ordinary case of "someone improved a script yesterday" fixes itself silently.

**One rule covers everything it prints: a `STOP` banner halts the pass, and every other line is
context to act on and carry into what you report.** Those lines say what they mean and need no
decoder here — a pull that did not land, a `-dirty` or `-dirty?` marker, a check that could not
run, a reference doc to re-read. **None of them is a reason to stop.** The older tooling still
works, and a run nobody else can reproduce should arrive with the reason already attached.

The banner is the exception, and it is not a judgement call:

- **Stop. Tell the user to `/clear`, then to ask for the release-note work again in the fresh
  session.** Do not continue, and do not offer to continue. A skill body is loaded into the
  conversation and stays there, so the pulled file does not replace the copy already in front of
  you — you would be running new scripts against old rules with no way to tell which of your own
  instructions were wrong.
- **`/clear` is enough. Do not tell the user to quit Claude Code.** It starts a new conversation
  with empty context and rebuilds the system prompt, so the next invocation reads the new file.
- **Only you can see any of this**, so relaying it is the whole job. `check-updates` also exits 1
  on a stale skill, so it reads as a failed command rather than a paragraph.

Drop `--pull` to ask the same question without changing anything, and add `--quiet` to print
nothing when there is nothing to act on.

## Run it: one command

`daily-pass.py` runs the scan, the preference delta and the clustering over a **guaranteed-identical**
range, adds the Bugzilla signals no single script owns, and reads your watchlist. Use it rather than
invoking the three scripts by hand — running them separately is how their windows drift apart.

```
python3 scripts/relnotes/daily-pass.py --build-day 20260801 --outdir /tmp/day01 --brief
```

- **`--brief`** prints the funnel and headline signals and writes the full report to
  `<outdir>/report.txt`. Use it and then **read the files** — do not redirect output with `>`, which
  is a file write needing its own approval, and do not pipe through `grep`/`head` when the Read tool
  will do.
- Everything lands in `<outdir>/`: `report.txt`, `scan.txt` (survivors with their landings),
  `dropped.txt` (the complete drop list to audit), `prefs.txt`, `clusters.txt`, `flags.json`,
  `scan.json`.
- Window flags are the same as `scan-window.py` (`--build`, `--build-day`, `--from-build`, `--cycle`,
  `--since-last`), plus `--save-state` to record the watermark on success.
- **Pass `--save-state` to this call rather than re-running `scan-window.py` afterwards to save it.**
  `daily-pass.py` writes the watermark from the scan it already did; a second `scan-window.py
  --save-state` re-enumerates the window and re-fetches every bug to record one line of state. A pass
  did this and refetched 219 bugs for nothing.

**Open every pass with the carried-over work, before the new window.** `resume` on the condition given
above; `followup` and `replies` every time, in this order. `replies` is not optional — it is the only
one that reads what developers actually said since you asked:

```
watchlist.py check-updates --pull        # self-update first; exits 1 and shows a STOP banner if /clear is needed
watchlist.py resume                      # briefing: where the last pass stopped, and the TOOLING line
watchlist.py followup                    # asked/replied bugs: nominated, awaiting, or needs chasing
watchlist.py replies                     # what people said on asked/replied/watching bugs since we asked
```

**`followup`'s section headings are instructions, and its output is not a status report to skim.**
Every `NEEDS CHASING` line and every `[follow up after <date>]` marker has to be resolved in your
report — chased, closed, or explicitly deferred with a reason. Twice now a pass has printed that
output and then contradicted it: 2058436 was reported as needing its needinfo closed when `followup`
had already shown it clear, and 1699444 sat under `NEEDS CHASING [follow up after 2026-08-08]` while
the report called it unresolved, when the developer had approved the wording that morning. Running
`replies` answers the second case directly; nothing answers it if you skip it.

**Then keep the watchlist current**, because it is the only memory between passes:

```
watchlist.py check-updates --pull        # self-update; drop --pull to only report, --quiet to speak only when acting
watchlist.py summary                     # per-release counts and days reviewed
watchlist.py list --status asked         # or declined, gated, noted -- implies --all
watchlist.py add <bug> --status asked --note "<what and when>"       # asked | declined | gated |
watchlist.py add <bug> --status declined --note "<why>"             # watching | noted | done
watchlist.py add <bug> --status watching --due 2026-09-01   # sets the "follow up after" date that
                                                            # resume and followup both display
watchlist.py decline <bug> --note "<why>"   # ONLY for a bug already on the list -- see below
watchlist.py noted <bug> --note "<where it shipped>"   # the note is live in Nucleus
watchlist.py log "<pass summary>"        # release-level context; resume replays these
watchlist.py days 20260801               # record the day as reviewed
watchlist.py rm <bug>                    # delete the entry outright -- see the caveat below
```

**`rm` deletes the entry and everything recorded on it**, with no confirmation and nothing to undo:
its summary, its `--note` trail and its due date all go. The watchlist is per-user, so that history
is the only record that this bug was already judged — losing it is how a bug already declined gets
re-proposed next cycle. Use `add --status <verdict> --note "<why>"` to change a verdict, and `rm`
only for an entry created in error, such as a typo'd bug number.

**Record a verdict with `add --status <verdict>`, not with `decline`/`noted`/`asked`.** Those short
forms change the status of something already tracked, and a bug you judged during this pass has
never been tracked — so they exit 1 with `is not tracked, so there is no status to change`. The
error names `add` as the recovery, which works, but it is two commands for every verdict. Reach for
the short forms only when following up on an entry a previous pass created.

**On `add`, `--note` also *becomes* the item's summary** — the one line `list`, `resume` and
`followup` display. So lead with what the bug is and put the verdict after it:

```
add 1921959 --status declined --note "Sidebar new tab button not clickable from screen edges. DECLINED 2026-08-05: 0 dupes, Nightly-only gate."
```

Not `--note "DECLINED 2026-08-05: ..."` — that listing never says which bug it is, and the whole
point of the entry is recognising it next cycle. The short forms differ here: they leave the summary
alone and append the reason to the log, which is why they are not interchangeable with `add`.

`bug-detail.py <ids> --landings A..B` shows each bug's landings in a window with their diffstat —
what a candidate actually changed, which is the usual reason to reach for the clone at all. Prefer it
over composing `git show --stat $(git log --grep=…)` by hand: a `$(…)` subshell defeats the permission
allowlist and prompts every time. It lists only commits whose *subject* names the bug and counts
cross-references separately, so another bug's commit is never reported as this one's landing.

`bug-detail.py <ids> [--comments]` gives the judgment fields for a candidate — version flags, relnote
flag, **open needinfo requests** (printed as `none` when there are none, so a clear bug is
distinguishable from an unchecked one), reporter and whether they are internal, resolved
duplicate/blocker summaries, regressor age, pending uplift requests. Reach for it instead of
assembling Bugzilla queries by hand. `--comments` adds comment 0 and the newest, truncated to 400
characters; `--comment N` (or `0,15,16`) prints those comments in full, which is what a developer's
reasoning usually needs.

## Step 1 — Enumerate and funnel (script does this)

```
python3 scripts/relnotes/scan-window.py --version 153 \
    --range FIREFOX_NIGHTLY_152_END..FIREFOX_NIGHTLY_153_END --format json -o /tmp/w.json
```

**Pass `--version N` for any historical window.** It defaults to the current Nightly, which checks
the wrong `cf_status_firefox{N}` field entirely when you're scanning a past cycle.

### Validating against whattrainisitnow.com

`https://whattrainisitnow.com/nightly/` is how Release Management currently hunts notes by hand, so
it's the reference for checking this skill's coverage — **not** an input the skill depends on.
`scripts/relnotes/nightly-buglist.py` pulls a build's or a day's list from it.

Two things about that list:

- **It does not filter backouts.** On build 20260731085738 it lists 57 bugs; 8 of those are not
  currently FIXED (backed out, never re-landed) and 7 are security-restricted. The funnel here
  reduces the same 57 to 29 survivors.
- **`--build <id>` reproduces its enumeration exactly** (57/57, nothing either way), because both
  resolve the same build boundaries. So a difference between this skill and a manual pass is a
  difference in *judgment*, not coverage — which is what makes the comparison meaningful.

Build boundaries resolve through two public hg endpoints (`json-firefoxreleases` for build id → hg
node, then `json-rev/<node>` for the `git_commit` field). `trainlib.py` handles this and caches the
12 MB build index for three hours.

### Uplifts change which version a note belongs to

A change that lands on Nightly N but gets **uplifted to Beta (N-1) or Release (N-2)** reaches users
in *that* version first, so **its note belongs to the version it was uplifted to, not N.** Those
ship sooner, which makes a missed uplift time-critical rather than a curiosity: a note owed to Beta
may be days from shipping while you file it against a Nightly six weeks out.

The scan checks `cf_status_firefox{N}`, `{N-1}`, `{N-2}`, and the live ESR versions, and flags any
survivor whose earliest landed version is below N:

```
2033733  Core :: Networking  [UPLIFTED -> note belongs to 154, also ESR 140]
```

Report the **target version per candidate**, not one version for the whole run.

**Merge-day windows straddle two versions.** The automatic version bump lands mid-window, so
commits before it belong to N-1 and commits after to N. The uplift check only sees "earliest landed
version is below the Nightly" and labels both cases `UPLIFTED`, which is wrong for the pre-merge
half — those landed in N-1 natively. `scan-window.py` now warns when a window contains a
`browser/config/version.txt` bump. Measured on 2026-07-20: 6 of 7 survivors were flagged as uplifted
and most had simply landed before the merge. Either way they are not the current Nightly's notes,
but do not describe them as uplifts.

**The catch: uplift status is not knowable when the change lands.** Approval comes days to weeks
later. Measured today, a 24-hour window had **zero** uplifted survivors, while the 153 beta-cycle
window had **40**. So:

- A daily pass will almost never see uplifts. Its version attribution is **provisional**.
- **Re-check version flags before notes are finalized**, not only at discovery. This is a re-check
  step in the workflow, not a one-time scan property.
- Say so in the output: state that uplift status was accurate as of the run and may change.

It fetches origin, resolves the window, extracts bug ids, batch-fetches Bugzilla, keeps only
currently-FIXED bugs (the real backout filter — reverts are flagged, not trusted), and drops
mechanically-never-noteworthy landings. It prints the funnel counts; **carry those into your
output** so the user can see the denominator.

**Audit the mechanical drop list too — every pass.** `daily-pass.py` always writes it complete to
`<outdir>/dropped.txt`, with the entry count in its header line — **Read that file.** Do not
reconstruct the list by piping `scan-window.py --show-dropped` through `sed`/`head`: a pass did
exactly that, received the 47 entries that fit under `head -95`, and reported that "all 86 mechanical
drops were walked". The drops are heuristics and they do get things wrong: bug 2047027, a real
Android tab-ungrouping menu item, was dropped as localization work because
`android-l10n-reviewers` appeared in the commit's `r=` list. Skim the dropped summaries for anything
that reads like user-facing work and rescue it. A false drop is invisible in the survivor list by
construction, so this is the only place it can be caught.

**Before reporting the audit, reconcile the count you actually read against the funnel's `mechanical`
number.** Both come from the same scan, so they always agree — if you have seen fewer entries than
the header announces, you are holding a truncated list and the audit has not happened yet. "No false
drops" is a claim about every entry; it cannot be made about the ones that scrolled off.

**At cycle scale, audit by group.** The file opens with a `SHAPE` table — every distinct drop reason
with its count, summing to the total — and then lists the entries grouped under each reason. A cycle
pass drops over a thousand, and reading that as a flat list is how one pass logged "1145 mechanical
drops, all audited" having never displayed the first 399 of them. Work down the groups, and say which
reasons you reviewed and how: "wpt-sync (620) sampled, the four behaviour-shaped groups read in full"
is an auditable claim, where "all audited" is not.

Judge the `landed:` lines, not the bug summary — a bug titled like test work often lands real
changes and vice versa. Use `--show-dropped` when the user wants to rescue something; the drop list
is auditable by design.

## Step 2 — Pref flips and gating (script does this)

```
python3 scripts/relnotes/pref-delta.py --range <start>..<end>
python3 scripts/relnotes/pref-delta.py --lookup browser.nova.enabled,layout.css.attr.enabled
python3 scripts/relnotes/pref-delta.py --lookup <pref> --rev FIREFOX_153_0_RELEASE
```

`--rev` resolves defaults at any revision instead of `origin/main`, which is how you answer "was this
gate on when 153 shipped" rather than "is it on now" — the question a dot-release or carry-forward
note actually turns on.

**A flip is only as meaningful as the feature it configures.** Check the *feature's* gate, not just
the flipped preference's own default. `browser.smartwindow.mistralRelease` flipped to `true` on every
channel — a textbook `FLIPPED ON` signal — while `browser.smartwindow.enabled` is `false` everywhere,
so nothing reached anyone. `pref-delta.py` now prints the enclosing `<namespace>.enabled` gates
beside each change; read them before treating a flip as a candidate.

**And check which version the feature actually landed in.** A flip can be the last step of work that
shipped in an earlier release: bug 2059112 only flipped the gate on bug 2047300, which landed in
**154** and was already region-gated. The note, if any, belongs to that version — the flip is not
news on its own.

**Run this on every window, and read its output before you walk the survivor list from Step 1.** The
steps are numbered in execution order — `daily-pass.py` runs the scan first because the funnel and
this share one window — but the *reading* order is the reverse: a flip reframes what the survivor list
means, so knowing what went live comes first. A feature's code often lands months
before it becomes live; the commit that makes it note-worthy is a one-line default change with an
unremarkable subject that no subject-based funnel will surface. `FLIPPED ON` is the strongest single
release-note signal there is.

The script resolves defaults per channel and platform from `origin/main`, handles the `@DEFINE@`
indirection, distinguishes *absent* from *false*, and collapses land/backout/re-land churn by
diffing window endpoints. Quote its `effective now:` verdict as **verified**; anything you infer
beyond it is inferred.

Preference state is not the whole gating story — check Nimbus and Labs per
`reference/release-notes/gating.md`.

**Resolve the gate before the evidence workup, not after.** Over three consecutive days
(2026-08-01..03) three separate candidates were killed by their feature's gate, and in every case the
full workup — reporter, duplicate count, blocking meta, landing message, precedent search — was done
first and then a single `--lookup` settled it:

| Bug | Gate | Verdict |
|---|---|---|
| 2047027 | Android tab groups, hardcoded `false` | not a note |
| 2060090 | `browser.ipProtection.enabled` false on all 12 configs | not a note |
| 2009909 | `dom.select.customizable_select.enabled` false on all 12 configs | not a note |

So: **if a survivor touches anything that looks preference-gated, `--lookup` its gate first.** Off
everywhere means watchlist, and the workup was wasted either way. One call answers it.

**"Looks gated" is too weak a trigger — resolve the gate for every feature-shaped survivor.** On two
consecutive days the gate was missed and came from the release manager instead: the
`mediaNotificationImprovements` Nimbus flag (2054954), then `browser.nova.enabled` (2043530). Neither
bug looked gated, and on both days the same check ran correctly on *other* bugs in the same pass — so
the rule isn't missing, it is being applied selectively. The two cases need different checks, and
neither substitutes for the other:

- **QA-filed bugs state the gate in comment 0**, in an explicit `**Preconditions**` block listing the
  prefs to set — 2043530's named `browser.nova.enabled = true`. `bug-detail.py <ids> --comments`
  prints comment 0, so add it to the batch call you are already making rather than deciding per bug
  which ones deserve it. **Read comment 0; do not pipe it through `grep`** — a pass filtered a
  `--comment 0` call for `see_also` and threw away the platform evidence in the same output. When the
  preview marks a cut, `--comment 0` gives the whole thing, and `--comment last:5` or `all` gives the
  discussion.
- **Developer-filed bugs usually say nothing at all.** All four comments on 2054954 are the
  developer's one-line summary, his patch, and two push notices; none mentions the gate, which
  existed only in `nimbus.fml.yaml`. **An empty Preconditions block is not evidence of no gate** —
  and neither is a failed fetch, which `bug-detail.py` now says out loud. For anything in a
  `Firefox for Android` or Fenix component, the FML check in `gating.md` is the check that answers it,
  and comment 0 cannot stand in for it.
- **A web-platform surface's gate is usually older than the change, and lives in `dom/webidl`.**
  Adding a method to an existing interface touches no preference file, so neither comment 0 nor the
  patch mentions a gate and both read as ungated. Bug 2057406 was asked and noted that way, and
  `StylePropertyMap` had been behind `layout.css.typed-om.enabled` (Nightly-only) all along. Grep
  `dom/webidl` for the API name — the webidl reverse lookup in `gating.md`, "Which preference is
  it?".

**A diff can prove a gate exists; it cannot prove that none does.** On 2043530 the report claimed
"verified ungated by reading the diff" because the button was added unconditionally in
`FormAutofillPrompter.sys.mjs` — but the gate lived upstream of that hunk, in the pref comment 0 named.
Reading a hunk establishes *no gate in that hunk*; the honest phrasing is "no gate found in `<file>`",
labelled **inferred**. Reserve **verified** for a gate you positively located: a `--lookup` verdict, an
FML block, a hardcoded `false`.

**A gated-off bug still has to appear in the report** — as a watchlist line naming the gate. Bug
2009909 was correctly excluded and then omitted entirely, so the user had to ask why a notable bug
was missing. "Correctly excluded" and "invisible to the reader" are different outcomes and only the
first is acceptable.

## Step 3 — Roll up features (script does this)

```
python3 scripts/relnotes/bug-tree.py --input /tmp/w.json --min-cluster 2
```

Work fans out: a New Tab widget is 6–7 bugs, a theming refresh 15+. **One candidate per feature,
listing every contributing bug** — never one note per bug. The script clusters by meta-bug ancestry,
whiteboard tag, summary prefix, shared subtree, and pref namespace, ranked by evidence quality
(multi-signal > meta > whiteboard/prefix > path).

Use its **completeness check**, which reads the meta bug's *full* dependency list rather than just
in-window members:

- `0 open` → the feature is complete; a note is in scope now.
- `N open` → still in progress; usually hold, but still worth asking the developer about timing.
- `dependencies landed OUTSIDE this window` → the feature predates the window. Say so; a cycle note
  may belong to an earlier version, and this is a common source of wrong-version notes.

It discloses what it refused to cluster (over-broad directories, tracking metas with >60
dependencies). Pass that disclosure through — those are coverage gaps, not absences.

## What the documented criteria say should be noted

From the nomination page, changes that belong in release notes:

* New features for end users **and web developers**
* Important changes for end users and web developers
* **Important stability and security fixes**
* Important system requirements changes (for example, end of support for an OS version)
* **New locales**

Two of these are easy to forget: system-requirements changes and new locales rarely look like
"features" in a commit log. Watch for them.

Note the tension with the calibration below: the criteria admit "important stability and security
fixes", but in practice the bar is high — Release Management's working guidance is that crash and
performance fixes are generally *not* called out for a major release unless there is a bigger story.
Both are true; "important" is doing the work.

## Calibration from real passes (read this before tiering)

**Read `reference/release-notes/calibration.md` before tiering.** It is the incident log for this
skill: every entry is a case where a pass was wrong and a Release Manager or the tree corrected it.
It carries the signals that argue for and against a note, the drop-audit lessons, and the gate
misses, and it outranks intuition on all of them.

## The `relnote-firefox` flag, including `nightly+`

Field: `cf_tracking_firefox_relnote`.

**Check it before proposing anything** — if it's already set, someone has made a call and the bug
doesn't need another ask. `daily-pass.py` reads the flag for every survivor, so a normal pass already
tells you. To go the other way and ask *which bugs carry a given value*, use
`relnote-flag.py` — `--nominated` for the `?` queue (see above), `--approved N` and `--nightly` for
what is already decided, `--declined` for the 234 bugs Release Management has said no to, which is
the only negative calibration corpus available. Those four are shorthands; `--value <v>` queries any
flag value directly, including ones no shorthand covers.

**`nightly+` is a real, used value** for changes enabled on **Nightly only** that are still worth
calling out, typically to invite testing and feedback. Verified examples: `Enable QUIC version
negotiation`, `Implement alpha() function behind pref`, `Enable link-parameters on Nightly`,
`Update tab context menu behind the pref in Nightly`.

**This changes how gated features are handled.** A Nightly-only preference-gated feature is **not
simply "hold"** — it is a candidate for a *Nightly* note. When a candidate resolves to "Nightly-only"
in `pref-delta.py`, ask whether it wants `nightly+` rather than filing it away. Keep the mainline
note for whenever it rides the trains.

## Step 4 — Judge significance and tier the output

Calibrate against the survey, not intuition. Then place each candidate in a tier that reflects
**how confident you are that it merits asking**, since that is what the user acts on:

- **Tier 1 — Ask the developer.** Clear user-facing change, or a pref flip making a feature live,
  or a complete feature cluster. You verified the mechanism.
- **Tier 2 — Probably worth asking.** Looks user-facing but you could not confirm scope, impact, or
  gating. Say exactly what you couldn't confirm.
- **Tier 3 — use sparingly, and expect it to be empty.** Across twelve calibrated days **not one
  Tier 3 item was ever accepted**, and the reviewer's verdict was "none of the tier 3 bugs look
  interesting". A long list of weak candidates is not cheap to skim — it is the main way this report
  wastes the reader's time. If the only thing you can say for a candidate is "plausibly notable,
  weak evidence", drop it with a reason instead. Reserve Tier 3 for something you genuinely expect
  to be overruled on, and if you have more than one or two, you are padding.
- **Watchlist — landed but not usable yet.** Parsing-only or phase-1 platform work, or a feature
  gated off on every channel. Not a note now; note it when it ships (the way JPEG XL only appeared
  once offered in Firefox Labs).
- **Dropped.** Report what the audit *found*, not an inventory of everything dropped — see the
  Output section. Plain text only: this is read in a terminal, so no HTML (`<details>`/`<summary>`
  renders as literal tags).

The recurring shapes that clear the bar, and the ones that never do, are characterized in the
survey's `Fixed`-threshold section. Two calibration anchors from it: **platform-scoped is not
disqualifying** when breakage is severe; **performance micro-wins and cosmetic corrections are
absent entirely** from two years of notes.

## Step 5 — Draft the one-liner, category, and screenshot call

**Read `reference/release-notes/style-guide.md` before drafting any wording** — not "consult it if
unsure", read it, every pass. Categories and the full rules live there.

**What follows is not a summary of that guide.** It is calibration from drafts that were rejected in
*this* workflow, and a rule appears here only because it was already missed once. Two rules that had
never been missed stayed in the guide and out of this list — spell abbreviations out ("Developer
Tools", not "DevTools") and use inline `code` for API names in Developer and Web Platform notes — and
a suggested wording then went out breaking both, on bugs 1856645 and 2060873, with the guide covering
them the whole time. A list that grows only where the skill has already been caught reads like a
checklist while being an incident log. The guide is the checklist; this is the incident log.

Keep drafts copy-pasteable; the user and the developer will edit. Median shipped note is ~20 words.

**Lead with the symptom the user saw, never the mechanism.** The most common drafting failure is
restating the patch. Two rejected drafts and their fixes:

| Rejected draft | Why it failed | Better |
|---|---|---|
| *Added a Windows font collection so more characters render correctly in web content.* | Describes the change (adding a font list entry), not the experience. "More characters" says nothing. | *Fixed characters from some less common scripts appearing as empty boxes on web pages on Windows.* |
| *Fixed text loss when typing with an input method on Linux, which could discard characters in some web applications.* | "Text loss" and "discard characters" are the same fact twice — the conflated-facts trap. Vague about where. | *Fixed text disappearing in Microsoft 365 Word Online on Linux when typing certain characters.* |

**Platform scope is a claim about the bug, not a field you can read off it.** A summary names the
platform the *reporter* was on. Bug 2053681 — *"…stops in Firefox Android when screen turns off"* —
was drafted as an Android-only note, and comment 0 listed Ubuntu too, so the wording had to be widened
after Release Management caught it. Before scoping a note to one platform, check the bug's own text,
its `see_also` links, and its per-version status flags. Once two platforms are genuinely in play, the
note is one object attached to both releases — see the cross-platform rule in `style-guide.md` — and
the platform lead comes off the wording entirely.

**Keep the suggested wording on one line.** Bugzilla wraps text itself; hard-wrapping the blockquote
just makes it awkward to copy.

**Cut the cause, keep the symptom.** Even an accurate mechanism clause gets trimmed. A proposed
*"Fixed Firefox preventing Linux systems from sleeping or suspending after a long browsing session,
caused by silent or muted videos holding a wake lock"* shipped as *"…after a long browsing session"* —
everything explaining *why* was removed. Across the drafts reviewed so far the editor has trimmed
several and lengthened none, so **write the shortest form that still identifies the symptom and who
saw it**, and leave the diagnosis in the bug.

**Naming a specific site is good practice, not a scoping error** — shipped notes do it (*"…on sites
such as Squarespace, LinkedIn, and eBay"*). If the bug names a site users recognise, use it.

Ask yourself: *what would the user have noticed before, and what do they notice now?* If the draft
can't be read that way, it's describing the patch.

### Where this fits in the documented process

The canonical process is
<https://wiki.mozilla.org/Release_Management/Release_Notes> — read it when a question isn't
covered here. Its "Daily during the Nightly cycle" step is exactly what this skill automates:

> Look through all patches that land in central via whattrainisitnow.com → identify if any patch is
> a candidate for release note nomination → needinfo the bug assignee and request if it should be
> considered for release note nomination.

Two things follow that the skill should respect:

- **The ask is a needinfo on the bug assignee**, not just a comment. The wiki's template is
  the nomination page. This skill adds suggested wording and uses the house phrasing — see the ask
  template below, which is the form to emit.
- **The daily pass has a second half this skill does not cover.** The process also says Release
  Management *monitors the `relnote-firefox` flag* for bugs developers nominated themselves, checks
  their wording and gating, and adds them to Nucleus. This skill only does the discovery half
  (scanning landings). Don't imply it covers the nomination queue.

**Nightly-only lifecycle**, from the same page — relevant to every `nightly+` candidate:

- Set `relnote-firefox` to `nightly+` and comment "Thanks, added to the Nightly release notes".
- The note must name the version that introduced it ("Starting with Firefox 113, nightly builds…"),
  which is why carry-forward notes cite an older version than the release they appear in.
- `nightly+` means the bug is included in Nightly release notes **for 3 cycles, or until the feature
  is enabled by default — whichever comes first.** At that point the flag is removed and the change
  becomes a normal release-note request against the version it ships in. Revisit
  `nightly-note-requested` watchlist entries on that schedule.
- `X+` means Release Management has decided the bug is in Firefox X's notes; `?` means nominated and
  awaiting their decision.

### Expect the wording to be revised — that is the process working

Developers and reporters will refine suggested wording most of the time. **This is normal and not a
defect in the ask.** The suggestion exists to give them something concrete to react to; a nomination
that arrives with better wording than you proposed is a success, not a miss. Do **not** tune toward
wording nobody edits — the way to achieve that is to write vague notes, which are worse notes.

So separate the two kinds of reply, and only learn from the first:

**Real errors — worth changing how you work:**

- **Direction reversed.** Bug 1699444's summary reads "Tabs opened by extensions are displayed as
  private when they are not". The proposed note said tabs are *no longer shown as private*. The
  actual fix is close to the opposite: extension-created tabs now **do** open in private browsing
  when the user is in private browsing, and private opening is **blocked** for extensions without
  the "Run in Private Browsing" permission. **A summary of the form "X is Y when it shouldn't be"
  can be resolved in either direction.** Read the patch to find out which; if you can't, write the
  ask without asserting a direction. This one was factually wrong, not merely differently worded.
- **Asserting scope you did not verify.** Bug 2051354's note said "accented or non-Latin
  characters"; the reporter said all Greek text was affected, including unaccented, and Google
  Sheets as well as Microsoft 365. The lesson is not the specific breadth — it is that scope
  inferred from a bug title and its blockers should be stated loosely, or flagged as unconfirmed,
  rather than asserted precisely and wrongly.

**Quibbles — expected, not lessons:** terminology preferences ("emulation" rather than
"simulation"), house phrasing, a clause added or dropped. Note them and move on; don't generalise a
rule from one person's stylistic preference.

Whatever the developer puts in `[Suggested wording]` supersedes yours by default.

### Emit the bug comment ready to paste

The point of a candidate is a request on the bug, so produce that text — don't make the user compose
it from a table.

**The single most important thing the ask must do is get `relnote-firefox` set to `?`.** Nomination
happens by flag, not by comment: Release Management monitors that flag, and setting it pops up a
form in the Bugzilla comment box asking for suggested wording and a documentation URL. **A reply
that doesn't set the flag is a nomination stranded in a comment thread** — nobody working the flag
queue will ever see it.

This is measured, not theoretical. Of the first eleven asks, three set the flag and two replied
enthusiastically *without* setting it, leaving those nominations invisible to the process.

So lead with the flag, link the instructions, and **needinfo the assignee** so it lands in their
queue rather than a comment that may go unread.

```
Did you want to nominate this for the Fx155 relnotes? If so, set the relnote-firefox flag to "?"
https://wiki.mozilla.org/Release_Management/Release_Notes_Nomination

Possible wording:
> Fixed characters from some less common scripts appearing as empty boxes on web pages on Windows.
```

The wiki's template reads *"could you please consider nominating this for a relnote?"* — **don't use
that phrasing.** "Did you want to nominate this" is the house voice: direct, and it reads as a
genuine question rather than a request being softened.

Properties that are load-bearing:

- **Ask for the flag explicitly.** "Set `relnote-firefox` to `?`" — not just "nominate this", which
  people reasonably answer in prose.
- **Needinfo the assignee**, so it lands in their queue.
- **Link the nomination page**, which explains the form they will be asked to fill in.
- **It asks, it doesn't assert.** The call stays with the developer.
- **It names the version.** Use the *uplift-adjusted* version, not necessarily the Nightly.
- **The wording is a blockquote**, clearly offered rather than imposed, and provisional.
- **It stays short.** No rationale or impact analysis; the ask must be cheap to answer.

**When note-worthiness hinges on an unresolved fact, ask the fact — not for a nomination.** Some
candidates are ambiguous in a way only the developer can settle, and a nomination request presumes
an answer. Bug 2057694 was asked as:

```
Does this mean we're shipping CT to Android Release for the first time? I'm having a hard time
finding a remote pref flip for it otherwise.
```

No template, no suggested wording — because whether there is any user-visible change at all was the
open question. Decide on the note once the answer arrives. Track these as `watching`, not `asked`.

**Emit exactly that block. Do not add explanatory clauses.** It is two lines plus the wording, and
it stays that way: no em-dash aside explaining what `nightly+` means, no extra paragraph naming the
preference or noting that `dev-doc-needed` is set. The developer knows their own feature; the ask
only has to be cheap to answer. If the change is Nightly-only, the one permitted variation is
"nominate this for a Nightly relnote" in place of "for the FxNNN relnotes".

Anything you learned that the developer might not know — the gating, the preference name, a related
bug — belongs in *your report to the release manager*, not in the bug comment.

(Setting the flag makes Bugzilla present the nomination form automatically, so there is no need to
ask for `[Why is this notable]`, `[Affects Firefox for Android]` and the rest — they come with it.)

**Follow up on stranded nominations.** A `replied` item whose flag is still `---` needs the flag set
(Release Management can set it) or a nudge — otherwise the work of asking is wasted.
`watchlist.py followup` lists every asked/replied item with its current flag state.

**Run `followup` in every pass, before you write anything asking the user to act.** Any line of the
form "close the needinfo", "set the flag", "withdraw the ask" is a claim about live Bugzilla state and
needs a live read — `followup` for the whole queue, or `bug-detail.py <bug>` for one, whose
`open needinfo:` line prints `none` explicitly so a checked-and-clear bug is distinguishable from an
unchecked one. **A watchlist status is never evidence of what is still open**; it records what *we*
did, and the developer or release manager may have acted since. A pass led its report with "the
needinfo needs closing" on bug 2058436, sourced from a stale `nightly-note-requested` status — the
needinfo had already been cleared, and the `followup` output that proved it had been printed in the
same session and misread.

**Then record the ask** so the next pass doesn't repeat it:

```
python3 scripts/relnotes/watchlist.py add <bug> --kind bug --status asked --release 155 \
    --note "<short description>; asked dev <date> re: Fx155 relnote"
```

Carry the **bug number(s)** alongside every candidate even though mainline notes don't link bugs —
the bug is where the user goes to ask.

Suggest a screenshot when a visual would materially help: new or restyled visible UI. Say no for
behavior, platform, and back-end changes with nothing to see. Briefly say why — it's a judgment
call, not a rule.

## Execution model

- **Daily:** run all three scripts, then deep-dive survivors inline. Small enough to be exhaustive.
- **Cycle:** run the scripts, then fan out subagents by area over the *ranked* clusters and the pref
  flips — not over the raw survivor list. Give every subagent the freshness rules (read prefs from
  `origin/main`), the truthfulness rules above, and the requirement to verify final FIXED state.
  Then synthesize.

Whichever you run, the funnel counts and the coverage caveats travel into the output.

**Don't edit this skill during a pass.** A pass produces candidates; it is not tool-work time.
Collect anything a run suggests and propose it after the window is closed and the verdicts are in,
so it never competes with the reviewer's attention while they are working the day's list.

**Expect to change this skill rarely.** Most verdicts are not skill bugs. Tiering is subjective and
the user does not expect this skill to be right every time, so a declined candidate with a reasonable
case behind it needs no rule added. Change the skill when a pass reveals a *fundamental* error that
got overlooked — a check skipped, a signal never looked at, something reported as verified that
wasn't — not because a judgment call went the other way. A rule per rejection turns calibration into
overfitting, and every added rule costs attention on every future run.

## Output

Lead with the funnel line (commits → bugs → FIXED → survivors → candidates), the window, and the
Nightly version and channel mapping used — then, **before any analysis**, a Bugzilla buglist link
covering every candidate, so all of them open in one go rather than one click at a time:

```
https://bugzilla.mozilla.org/buglist.cgi?bug_id_type=anyexact&bug_id=<comma-separated ids>
```

**That link belongs at the top, not down with the tiers.** The reader opens the bugs first and then
reads the analysis alongside them, so a link printed after the tier tables sits a hundred lines
below the moment it was wanted. `daily-pass.py` prints equivalents for the survivor and dropped
sets; build this one from your tiered candidates, and repeat it per tier inside each tier when the
list is long.

Then:

1. **Pref flips** — what became live or hidden, with per-channel defaults and bug numbers. First,
   because it's the highest-signal section.
2. **Tier 1 / Tier 2 / Tier 3 candidates** — **one block per candidate, never one wide table per
   tier.** Each candidate gets its own two-column table, field name on the left, and its reasoning
   goes immediately after it, before the next candidate begins:

   ```
   | | |
   |---|---|
   | **Bug** | **2061547** |
   | **Bugzilla summary (verbatim)** | `exactly as Bugzilla has it` |
   | **Component** | Core :: CSS Parsing and Computation |
   | **Category** | Changed |
   | **Draft note** | your proposed wording |
   | **Gated?** | the gate, or "no pref gate found" |
   | **Screenshot?** | yes/no and why |
   | **Basis** | verified or inferred, and what you actually checked |
   ```

   **A tier-wide table with those fields as columns is the other reading of this instruction, and
   it does not work.** Eight columns cannot hold a full draft note in a terminal, and the
   per-candidate reasoning ends up pooled underneath the table, detached from the row it belongs to —
   so the reader has to match paragraphs back to rows by bug number. They work one candidate at a
   time: read the summary, read the draft, decide, move on. Shape the output the way it gets read.

   **Always print the bug summary, quoted exactly as Bugzilla has it — do not paraphrase, tidy,
   or fix typos in it.** The user's next action is opening the bug to ask the developer, and the
   verbatim summary is how they recognize it and match it against what they see there. Reproducing
   it exactly also makes a wrong bug number obvious immediately. Keep it visually distinct from your
   *draft note*, which is your proposed wording — never let a reader mistake one for the other.
3. **Feature rollups** — cluster, member bugs, completeness, and whether to note now or hold.
4. **Watchlist** — landed but not usable yet.
5. **Drop-list audit findings** — *not* a list of everything dropped. A 45-item inventory of
   internals, perf and cosmetic drops is noise nobody acts on; what matters is what the audit turned
   up. Report only: any **false drop** (a real candidate the mechanical filter caught, with the
   pattern that caught it), any item **you could not resolve** and would want revisited, and a bare
   **count** of the rest by category — one line, e.g. "42 others dropped: internals 17, perf 2,
   telemetry 1, cosmetic 2, tests 20". If the reader wants the full list they will ask, or run
   `--show-dropped` themselves.

   **Plain text, no HTML.** The output is read in a terminal, so `<details>`/`<summary>` renders as
   literal tags rather than a collapsible block.

Close with a short methodology note: exact window, Nightly version, **that uplift status was
accurate only as of the run and needs re-checking before notes are finalized**, that the backout
filter is Bugzilla-FIXED (naming any land-then-reland cases), that pref defaults were read from
`origin/main`,
which lookups used MCP vs REST, how many bugs were security-restricted, **which mode you ran and
therefore what you did not look at**, and anything you could not determine and why.

Keep it a working queue, not an essay.
