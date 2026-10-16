---
name: nightly-cycle-review
description: Review a whole Firefox Nightly development cycle at the end of the cycle (around merge day). Summarizes what landed, which products, components and source areas changed most, the risks and benefits the release carries, and which specific bugs to monitor in Beta for regressions. Use when the user asks for a cycle review, an end-of-cycle or merge-day summary, "what changed in Nightly N", "what's risky in N", or "what should we watch in Beta N".
allowed-tools:
  - Bash(python3 .claude/skills/nightly-cycle-review/scripts/cycle-data.py*)
  - Bash(python3 .claude/skills/nightly-cycle-review/scripts/cycle-signals.py*)
---

# Nightly cycle review

This review answers the questions Release Management asks at the end of a Nightly cycle:

1. **What parts of Firefox changed most?**
2. **Were there risky changes that may cause regressions or incidents?**
3. **Which specific bugs should be monitored in Beta** because they already caused regressions or are likely to?
4. **What does the release bring?** The notable benefits: features, fixes and performance work.

The readers are release managers. Write for people who know Firefox's release process but have not followed every bug.

## Truthfulness

This output drives decisions about what to watch and who to ping, so a confident wrong claim costs more than a gap.

- **Label the basis of every risk claim**: **verified** (you read the patch, the bug, the pref default) or **inferred** (from size, component, summary or other metadata only).
- **Never invent** bug numbers, prefs, crash signatures or versions.
- **"No regressions filed" is not "safe".** Regressions show up in Beta and release; say "none filed yet".
- **Report what you could not see**: security-restricted bugs, tool failures, heuristics that may be wrong.
- **Read the whole summary with the Read tool.** Don't sample it through `grep` or `head`; claims about "all" of something need all of it.

## Step 0: check the setup

Before the long runs, check what the scripts need, and tell the person what's missing and how to fix it. The review still works without these, but loses parts of the data.

- **Bugzilla API key** (needed for security bugs and the user-report section). Check that `~/.config/bugzilla/api-key` exists and has mode 600, or that `BUGZILLA_API_KEY` is set. **Never read, print or copy the key.** Test it without displaying it, e.g. a short Python snippet that reads the file and calls `https://bugzilla.mozilla.org/rest/whoami` with the `X-BUGZILLA-API-KEY` header, printing only success or failure. If it's missing or rejected, give these steps:
  1. Create a key at https://bugzilla.mozilla.org/userprefs.cgi?tab=apikey.
  2. In **their own terminal**, not through a `!` command in Claude Code, which can't read input and would put the output in the session:
     ```
     mkdir -p ~/.config/bugzilla && chmod 700 ~/.config/bugzilla
     read -rs KEY && printf '%s' "$KEY" > ~/.config/bugzilla/api-key && unset KEY
     chmod 600 ~/.config/bugzilla/api-key
     ```
     `read -rs` waits silently for the pasted key. Never ask them to paste it into the chat.
- **`gh` logged in** (webcompat.com reports): `gh auth status`; otherwise `gh auth login`.
- **`socorro-cli`** (crash pings): if it's missing, run `./mach bootstrap` in the Firefox checkout.
- **Gecko checkout**: resolved like every Relman script (`--repo`, then `$RELMAN_GECKO_REPO`, then the path saved by `python3 scripts/relnotes/watchlist.py check-setup --repo /path/to/firefox`). If it isn't set up, the scripts exit with that instruction.

Both scripts repeat these checks and print the same instructions if something is missing. They also mark an unauthenticated run at the top of `summary.md` and `signals.md`. If a run was unauthenticated, say so in "Limits of this review".

## Step 1: choose the cycle

The Nightly cycle for version N is `FIREFOX_NIGHTLY_{N-1}_END..FIREFOX_NIGHTLY_{N}_END`. There is no `_BASE` tag for Nightly, and `FIREFOX_BETA_{N}_BASE` is the same commit as `FIREFOX_NIGHTLY_{N}_END`. Don't derive cycle bounds from dates.

- **Finished cycle** (the usual case, right after merge day): pass `--version N`.
- **Cycle still in progress** (a pre-merge look): pass `--version N --range FIREFOX_NIGHTLY_{N-1}_END..origin/main`, and say in the report that the cycle is incomplete.

If the version is ambiguous, ask which one. Run `git -C <gecko checkout> fetch origin --tags` first if the `_END` tag is missing.

## Step 2: collect the data

```
python3 .claude/skills/nightly-cycle-review/scripts/cycle-data.py --version N
```

It takes about 2 minutes for a cycle of about 4,000 commits. It writes to `<system temp>/relman-cycle-review/cycle-N/` (or `$RELMAN_CYCLE_OUTDIR/cycle-N/`, or `--outdir`), outside the working tree, and prints the path:

- `summary.md`: aggregates and ranked tables. **Read all of it.**
- `cycle.json`: every bug (component, type, severity, status, keywords, `status_n`/`tracking_n` for version N, landings, backouts, lines by kind, top source areas, last landing date, and the full regression list with each regression's status, severity and `status_n`), plus every commit. For questions the tables don't answer, write a small Python script to `/tmp` and query this file.

Options: `--top` sets rows per capped table (default 25), and `--late-days` sets the late-landing window (default 3 days; cycles are currently two weeks). Security-restricted bugs need a Bugzilla API key, read from `BUGZILLA_API_KEY` or else from `~/.config/bugzilla/api-key`. Without one they appear as "(restricted)". Never print, log or copy the key.

**Confidential bugs in an authenticated run.** `cycle.json` and `summary.md` then contain security bug summaries and components, so keep them in the output directory, never in the repo, and don't share them. In `review.md`:
- Count security bugs by component or area only, e.g. "12 security fixes in JS JIT". Don't give their bug numbers, summaries or mechanisms.
- If a security bug is itself a Beta risk (large, late, has regressions), describe the risk without the vulnerability, e.g. "a large late security fix in WebRender", and give the details to the user in the conversation only.
- `authenticated: true` in `cycle.json` means the run could see them; say so in "Limits".

How the script measures:
- **Line counts** are added plus deleted lines from non-revert commits, split into `code`, `test` and `vendored` by path heuristics. Large entries can still be generated or mechanical (bindings, license data, sweeping renames), so check the top rows before you call an area "the most changed".
- **Backouts** are `Revert "..."` / `Backed out` commits, attributed to the bug in their subject.
- **Regressions** come from each bug's `regressions` field. Those filed before the cycle started (older landings of the same bug) are excluded from the counts but kept in the JSON as `before_cycle`.
- **`in_tree`** says whether the bug's work is actually in the release. It compares the bug's files between the cycle's start and end: `no` means none changed, and `mostly-not` means under half changed. **Check it before naming anything as a risk.** Revert commit subjects are unreliable: Lando reverts a whole stack under the subject of one commit, so "backed out once, relanded" often means "backed out for good". In the 159 run, the five scariest-looking bugs (GC nursery, WebRTC FIPS, IPC refcounting, sidebar refactor, session-restore focus) were all absent from the release. When a bug's newest commit is a revert, the script also checks its last landing by applying the patch, forwards and in reverse, to the merge-tag copies of its files, with a blob comparison as fallback. Files shared with other bugs can make the first check say `yes` wrongly. In 159 this hid one backed-out feature and one backed-out security fix, so the release would have been described as containing a security fix it doesn't have. A bug that is `RESOLVED FIXED` but has `status_n` = `affected` is the same pattern: check whether the fix relanded only after the merge, because that is an uplift candidate. Backouts are recognized in both the `Revert "..."` form and the `Bug NNN: Backed out ...` form. For any other borderline case, compare blobs by hand: `git rev-parse FIREFOX_NIGHTLY_{N}_END:<path>` against `<sha>:<path>` and `<sha>^:<path>` for the files of its last landing.

## Step 2b: collect the external signals

```
python3 .claude/skills/nightly-cycle-review/scripts/cycle-signals.py --version N
```

Run it after `cycle-data.py`; it reads `cycle.json`. It takes about 2 minutes and writes `signals.md` (read all of it) and `signals.json`. Use `--skip prefs|nimbus|perf|crashes|crash_pings|intermittents|test_disables|webcompat|open_issues` to leave one out (the skipped sections keep their previous results). It uses this repo's `scripts/relnotes/pref-delta.py` and the same output directory as `cycle-data.py`.

- **Pref defaults** come from Relman's `pref-delta.py`, resolved for Nightly, early Beta, late Beta and release on each platform. `flipped` (a default that changes on Beta or release) is the list to check against Step 3's gating. It is more reliable than searching bug summaries for "enable"; in 159 it found four networking defaults the summary search missed. `new-on` prefs are new code paths; `removed` ones usually mean a feature became permanent.
- **Nimbus** lists live rollouts and experiments (Experimenter API) that can target N on Beta or release. Use it to correct "ships dark" claims: a feature that is off in the tree can be on for part of the population. It also shows prepared kill switches (not-started rollouts that turn a feature off) and concurrent rollouts in the same area as a risky change. Messaging-only rollouts are counted, not listed.
- **Perfherder** lists alert summaries pushed during the cycle, linked to cycle bugs when the alert's bug is a regression of one. Most open alerts are untriaged and unlinked; report them as such and don't guess a cause. Improvement-only summaries feed Step 6.
- **Crashes** compare Nightly N with Nightly N-1 over windows of the same length, by build and crash date. A signature is `new` (no installs before) or `rising` (3x the installs), with at least 10 installs. `Last build` includes the next cycle's Nightlies, so `active: no` means the crash stopped, usually because of a fix or backout before merge; check that the fix is in the merge tag. For an active crash, look at its linked bugs and their `regressed_by`. If it has no bug and matches a cycle bug's code, that's an action item. After b1 has shipped, the `crash-analysis` skill can compare Beta N with Beta N-1.

- **Crash pings** (`socorro-cli crash-pings`, opt-out telemetry) compare the share of each signature in N.0a1 pings this cycle with (N-1).0a1 pings in the previous one. They show the real crash experience, including users who never submit reports. However, ping signatures don't always match Crash Stats signatures, so a ping-only "new" can be an existing crash under another name (in 159, `nsThreadPool::PutEvent` looked new in pings but had 54 installs in 158 reports). Confirm in Crash Stats before reporting a signature as new, and look at its `build_id` facet: a burst in a few builds that stops is usually already fixed. Use pings mostly to confirm the size of a rise (in 159 they corroborated the `ShutDownKill` increase).
- **Intermittents** (Treeherder failure classifications per bug) list test failures that are new or 3x more frequent on trunk. Those regressed by a cycle bug come first. A cluster of new intermittents in one area points at fragile code in that area (in 159: AI window tests). Infrastructure failures (hg, taskcluster, Talos harness) are noise for release risk.
- **Test manifest conditions** diff `skip-if`, `fail-if` and similar lines between the cycle tags, grouped by directory. A positive net means tests were disabled or marked failing during the cycle. Read the cited bugs for the top areas. Web-platform-test expectation churn is normal and mostly sync-related.

- **Webcompat** has two parts:
  - **Bugzilla bugs with a `webcompat:*` keyword** filed during the cycle. They are flagged when regressed by a cycle bug, when the autowebcompat bot reproduced them on Nightly but not on stable (`autowebcompat-repro-channels` in the user story), or when they are affected in N and unaffected in N-1. Follow `dupe_of` for flagged duplicates: the real platform bug is usually the target (in 159, Bug 2075543 led to Bug 2072085).
  - **webcompat.com reports from Firefox N users** (GitHub `webcompat/web-bugs`, needs `gh` logged in). `Other reports` counts reports for the same domain from other versions in the window; 0 hints at a version-specific problem but proves nothing until triaged.

  Use this to check the web features switched on by default (Step 3) and in "Benefits". Telemetry from the "Report Broken Site" button would be the best source, but it is on STMO and needs a `REDASH_API_KEY`.

- **Open defects filed by users** during the cycle, in the main desktop and Android products. Reporters in the `mozilla-corporation` or `editbugs` Bugzilla groups (employees and trusted contributors) are excluded with the `%group.mozilla-corporation%` and `%group.editbugs%` pronouns, which REST accepts when the request is authenticated. Bot accounts are excluded too. Use this section for:
  - **Regressions with no known cause.** Try to match them to a cycle bug (area, timing, Step 4b code reading). A match is an action item.
  - **S1/S2 user reports** that may ship in Beta N. Most new bugs have no `status_n` yet, so judge from the component and the filing date.
  - **Clusters**: a component with an unusual number of user reports can be an unlinked regression. Check whether the cluster comes from one reporter first: bulk filers (5+ bugs in the window) are usually code audits or tool output, not users hitting a problem (in 159, one reporter filed 28 Find bugs).
  - **Security reports from users**: counts by rating and area only, under the security rules below.

Security: crash signatures and perf-alert bugs can point at a security fix (through `regressed_by`, or a signature naming its code). Check before naming them in `review.md`, and describe them generically if they do.

## Step 3: what changed most

Combine **bug counts** (breadth of activity) and **code lines** (size of change). Neither is enough alone.

- **Separate product change from infrastructure.** Testing, Developer Infrastructure, Build System, Task Configuration, web-platform-tests syncs, lint and docs work matter to developers, not to users. Report them in one line, not as headline areas.
- **Recognize vendored updates** (ffmpeg/ffvpx, libwebrtc, ONNX Runtime, Glean, application-services, wgpu, PDF.js) as a category. They are big and often risky, but they are not in-house design changes.
- **De-emphasize New Tab work that train-hops.** New Tab ships updates through the Train Hop add-on, independently of the release train, so its changes are not tied to cycle N: they can reach Release earlier through a Nimbus `newtabTrainhopAddon*` rollout, and a New Tab problem can be fixed the same way without an uplift. Only code under `browser/extensions/newtab/` train-hops (bugs with `train_hop: true` in `cycle.json`). Give that work one line in "What changed most", don't count it towards headline themes, and don't put it on the watchlist unless it has an open regression with no add-on fix. **Code in `browser/components/newtab/`, platform changes New Tab depends on, prefs in `browser/app/profile/firefox.js`, and Glean metric schema changes still ride the trains**, so assess those normally. The in-tree reference is `browser/extensions/newtab/docs/v2-system-addon/train_hopping.md`.
- **Group into themes** by reading summaries across components: a feature program usually spans several components and dozens of bugs. Name the theme and give its bug count and main bugs.
- **Check gating for the big themes.** Is it on by default in Beta N, Nightly-only, or behind a pref or Nimbus? A Nightly-only feature does not ride to Beta, which changes both its risk and its benefit. Verify against the merge-day tree, e.g. `git -C <gecko checkout> show FIREFOX_BETA_{N}_BASE:modules/libpref/init/StaticPrefList.yaml`, `browser/app/profile/firefox.js` or the relevant `#ifdef NIGHTLY_BUILD`. If you didn't check, say "gating not verified".

## Step 4: risk

Rank candidates using these signals. A bug with several signals matters more than one with a single strong signal.

| Signal | Where it is in the data |
|---|---|
| Open regressions filed against it, especially S1/S2 or tracked | "Cycle bugs with regressions" table |
| Many regressions already, even if fixed: a fragile change | same table, total column |
| Backed out more than once, or still not RESOLVED FIXED | backout and "not fixed" tables |
| Large code change, especially landed in the last days before merge | largest and late-landing tables |
| Sensitive area: JS JIT/GC, WebRender and graphics, layout, DOM core, networking, media and codecs, IPC, sandboxing and security, startup and profile, updater and installer, crash reporting, session restore | component and area columns |
| Major vendored update (see Step 3) | largest table, component |
| Defaults flipped on for all channels during the cycle | needs checking (Step 3 gating) |

Then **deep-dive the top candidates** (about 10 to 15) before naming them as risks. Drop anything whose `in_tree` is `no` or `mostly-not`, and list it in the report as "backed out, not in N" so readers don't worry about it.

- Discount automated noise: Coverity follow-ups filed as regressions often say "no bug today", and perf alerts can be measurement artifacts. Read the developer's analysis before counting either.

- Read the bug and its regressions with the `moz` Bugzilla tool: what broke, on which platforms, is a fix in progress, any crash signatures.
- **Find regressions that affect N and are fixed only on central.** Look for regressions with `status_n` = `affected`, or fixed after merge day. Those need a Beta uplift, which makes them action items, not just things to watch. Query `cycle.json` for regressions where `status_n` is not `fixed`/`verified`/`unaffected`/`wontfix`/`disabled`.
- Check whether the change is gated (Step 3). A risky change that is Nightly-only is not a Beta risk.
- For crash-prone areas, the `crash-analysis` skill can check whether known signatures spike on Nightly N. Use it only for the few top candidates; it is slow.

## Step 4b: read the code of the top candidates

Metadata says a change is big, late or fragile. Only the diff says what it actually does. For the top risk candidates still standing after the deep-dive (about 8 to 12, `in_tree` = `yes`), read the landed code.

**Find the right commits.** `cycle.json` lists each bug's commits newest first, with a `revert` flag. Keep the landings after the last revert; earlier landings were backed out and relanded. If a bug ends on a revert, check it with the blob comparison (Step 2) before reading anything: it may not be in the release.

**Read efficiently.** Start with `git -C <gecko checkout> show --stat <sha>`, then read the product code: `git show <sha> -- <paths>`. Skip tests, vendored code and generated files unless they are the point. For context outside the diff, read the file at the merge tag (`git show FIREFOX_NIGHTLY_{N}_END:<path>`) or use `searchfox-cli`. For a diff over about 1,500 lines, read the core files and say which parts you skipped.

**Answer these for each bug:**
1. **What behaviour changes, in plain words**, and for whom: platforms (`#ifdef`, `XP_*`, `MOZ_WIDGET_*`), channels (`NIGHTLY_BUILD`, `EARLY_BETA_OR_EARLIER`), prefs or Nimbus features, and their values in the merge-day tree.
2. **What could break:** new release asserts or crash paths, threading or locking changes, new IPC, lifetime and ownership changes, error paths that are now fatal or now ignored, data or profile migrations that can't be undone, changes to defaults.
3. **What protects it:** a pref to turn it off, a fallback path, test coverage in the same patch.
4. **Does the code match the bug's claims and the regressions filed?** For example, does a crash stack point at a line this patch added?
5. **Verdict:** keep, raise or lower the risk ranking, with one sentence of reasoning that cites the file and function, not line numbers.

Claims that come from reading the code are **verified**. Say which files you read.

**Running it.** Use the Agent tool to read several bugs in parallel, 2 or 3 bugs per agent and usually 3 or 4 agents. Give each agent the bug numbers, the commit shas to read, the merge tag, the checklist above, and instructions to stay read-only and to write nothing outside `/tmp`. Check the agents' conclusions against each other and against the bug before using them. Never pass security bug details into a shareable report (see Step 2).

This is a first look for release risk, not a code review. Don't comment on style or code quality.

## Step 5: Beta watchlist

Give a short, concrete list, usually 10 to 20 bugs. For each entry:

- the bug number, component and a one-line description in plain words;
- **why it's on the list**: which signals, marked verified or inferred;
- **what to watch in Beta**: a crash signature, component, telemetry, webcompat reports or a platform;
- **action**, if there is one: "uplift fix from bug X", "ask the developer whether the pref should ride", or "monitor only".

Order it with action items first, then by risk.

## Step 6: benefits

From the visible bugs, pick out the notable features that ride to Beta (gating verified; New Tab add-on features train-hop, so don't present them as benefits of N), user-facing fixes for long-standing problems, performance and memory work, regression fixes (the count of fixed bugs with the `regression` keyword is in the summary) and platform support changes. Count security-restricted bugs as security and stability work without describing them.

## Output

Write the report to `review.md` in the output directory and give the user a short version in the conversation (headline, top risks, watchlist). Report structure:

1. **Headline**: 3 to 5 sentences: size of the cycle, the main themes, overall risk level and why.
2. **Numbers**: commits, bugs, backouts, regressions filed (open or total), restricted bugs.
3. **What changed most**: themes and areas, product work separated from infrastructure.
4. **Risks**: ranked, each with its basis (verified or inferred).
5. **Beta watchlist**: the Step 5 table.
6. **Benefits**.
7. **Limits of this review**: restricted bugs, unauthenticated run, heuristics, what was not checked.

Link bugs as `Bug NNNNNN`. No emoji. Never post anything to Bugzilla or elsewhere; this review is a draft for the release manager running it.

## Maintenance

- The Gecko checkout and Bugzilla access follow the Relman conventions: `trainlib.resolve_repo` for the checkout, `~/.config/bugzilla/api-key` or `BUGZILLA_API_KEY` for the key. `scripts/setup_check.py` holds the shared checks.
- Lessons from real cycles are written into the steps above, each with the cycle that taught it (159 so far). When a new cycle shows a heuristic flagging noise or missing something, add the example in the same place.
- The data sources depend on external APIs (Bugzilla, Crash Stats, crash-pings, Treeherder, Perfherder, Experimenter, GitHub). If one changes, its collector fails on its own and the report says "not collected"; the rest still runs.
