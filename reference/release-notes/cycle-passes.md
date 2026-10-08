# Cycle, census and other non-daily passes

Moved out of `find-release-note-candidates/SKILL.md` because a daily pass never needs it. Read this
before any pass that is not the daily forward one.

## Cycle tags

**The repository's cycle tags are the authoritative boundaries.** Don't infer a cycle from release
tags, dates, or `version.txt`; the tags exist for exactly this:

**Nightly cycle for version N (use this, it is validated):**

```
FIREFOX_NIGHTLY_{N-1}_END..FIREFOX_NIGHTLY_{N}_END
```

Note there is **no `FIREFOX_NIGHTLY_{N}_BASE` tag**: only `_END` exists, so the cycle start is the
previous version's `_END`. `FIREFOX_BETA_{N}_BASE` is the same commit as `FIREFOX_NIGHTLY_{N}_END`
(verified identical SHA), so either works as the closing boundary.

**Beta cycle for version N (work also lands during beta, so version coverage requires it):**

```
scan-window.py --version N --first-parent \
    --range FIREFOX_BETA_{N}_BASE..FIREFOX_RELEASE_{N}_BASE
```

Three things about this range, each verified against the 153 cycle:

- **`--first-parent` is mandatory.** It restricts the walk to the beta branch's own chain. Without
  it the range pulls in every merged `main` ancestor: **71,678 commits instead of 682.**
- **The closing boundary is `FIREFOX_RELEASE_{N}_BASE`, not `FIREFOX_BETA_{N}_END`.** In the git
  mirror `FIREFOX_BETA_153_END` points at a merge-day config commit dated the *same day* as
  `_BASE` ("No Bug - Update configs after merge day operations"), so it sits at the **start** of the
  beta cycle, not the end. This differs from the hg tag of the same name; don't assume the mirror's
  tags match hg's.
- **It matches the hg pushlog.** Cross-checked against
  `hg-edge.mozilla.org/releases/mozilla-beta/json-pushes?fromchange=FIREFOX_BETA_153_BASE&tochange=FIREFOX_BETA_153_END&full=1&version=2`:
  the git range **contains every bug hg reports**, plus a few more. A superset, so it errs toward
  inclusion.

Uplift commits carry an `a=<approver>` marker (`Bug 2033733 - enable LNA for all desktop users by
default. a=pascalc`). Both 153 uplifts that earned notes were **preference flips**, so run
`pref-delta.py` across the beta endpoints too, not just the nightly ones.

## Other passes: the cycle rollup, the census, and other people's nominations

The daily forward pass is in `SKILL.md`. These run on their own schedules and none of them is a
window choice: the rollup, the census and the policy-template check are end-of-cycle work, the
nomination queue starts from Bugzilla rather than from what landed, and the beta-uplift mode
produces candidates for a different release's owner.

### End-of-cycle rollup check

Long-running clusters that were deferred all cycle need one deliberate pass before the merge:
**run `--cycle N` near the end of the Nightly cycle and revisit every cluster that was on hold**, to
decide whether the finished body of work now deserves a single rollup note. Interop work, multi-bug
feature pushes and preference-gated features that flipped late are the usual candidates. Track
deferrals in the watchlist (`--status watching`) so they resurface rather than being rediscovered.

**Once a cycle has shipped, retire its state.** Re-check each open entry's gate, `carry` the ones
still gated into the current release, and `drop-release` the old one. The drop refuses while open
entries remain (`--force` overrides), which is the point: those are what the daily pass re-surfaces.

**Then check coverage from Bugzilla's side**, which is the one question a window scan cannot answer
about itself:

```
python3 scripts/relnotes/scan-window.py --cycle 155 --version 155 --census
```

`--census` searches for every bug Bugzilla flags as landed in the version and reports the ones no
commit in the cycle mentions. It refuses on anything narrower than the full cycle, because a partial
window reports the rest of the cycle as unseen. `daily-pass.py --census` does the same and leaves it
in `census.txt`; on a `--format json` run, `--census-out PATH` writes the readable section. Most of
what it finds is explained rather than missed, and it sorts what it finds into buckets that say
so: mechanical, flagged for an earlier version as well (QA sets `verified` on the version they
*tested*), no landing of their own, and every landing an ancestor of `FIREFOX_NIGHTLY_{N-1}_END`
(landed by the end of the previous cycle whatever the flag says; on the 159 census both residue
bugs were this, and one led to a Nightly-only feature with no Nightly note). What survives all of
those is **a handful** out of thousands flagged, and that residue is where a **beta uplift** shows
up: those commits live on the beta branch, so no scan of main can see them however wide the window.

### Cumulative passes

Use `--cycle N` for the wider sweeps where notes have no daily granularity: feature rollups that
only become visible across weeks, and preference flips that make earlier work live. Run
`bug-tree.py` and `pref-delta.py` over the same range.

### The nomination queue: bugs someone else proposed

Discovery works forward from what landed, but developers and triagers also nominate bugs directly by
setting `cf_tracking_firefox_relnote` to `?`. Those never appear in a window scan unless they happen
to land in it.

```
python3 scripts/relnotes/relnote-flag.py --nominated
```

**Defaults to nominations on bugs that are actually fixed, and that default matters.** Most `?` bugs
are open (a developer pre-registering an intention months ahead), and the team treats those as noise
rather than decisions waiting to be made, so the fixed subset is usually a small fraction of the
queue. `--include-open` shows the rest if you specifically want the pipeline view.

Work a fixed nomination exactly like a candidate you found yourself: the bar, the tiering, the
precedent search and the gating checks in `SKILL.md` all apply unchanged. The only difference is
that someone has already argued it deserves a note, so the question is whether you agree, and the
answer is a comment in the bug rather than a proposal in a report.

### The beta-uplift mode: a future mode worth knowing about

Running this skill over the beta cycle's uplifts, to prompt that release's owner. Same machinery,
different window (`--range FIREFOX_BETA_{N}_BASE..` with `--first-parent`), and the audience is the
beta owner rather than you.

### Backfilling old windows vs. the normal forward pass

Normal use is **forward, one day at a time**, where the tree state and the window state coincide.
Working *backwards* through past days, as during calibration, introduces a hazard that never
arises going forward: preference state can have changed between the window and today.
`pref-delta.py` handles this by reporting `at window end:` alongside `effective now:` and flagging
when they differ, but the wider point holds for anything read from `origin/main`. When backfilling,
treat "what is true today" and "what was true then" as separate questions.

### Validating against whattrainisitnow.com

`https://whattrainisitnow.com/nightly/` is how Release Management currently hunts notes by hand, so
it's the reference for checking this skill's coverage, **not** an input the skill depends on.

Two things about that list:

- **It does not filter backouts**, and it counts security-restricted bugs. The funnel here removes
  both, so its survivor count sits well below the length of that list.
- **`--build <id>` reproduces its enumeration exactly**, because both resolve the same build
  boundaries; `trainlib.py`'s header records the check. So a difference between this skill and a
  manual pass is a difference in *judgment*, not coverage, which is what makes the comparison
  meaningful.

Build boundaries resolve through two public hg endpoints (`json-firefoxreleases` for build id → hg
node, then `json-rev/<node>` for the `git_commit` field). `trainlib.py` handles this and caches the
build index for three hours.
