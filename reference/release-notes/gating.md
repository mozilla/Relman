# Is it actually reaching users? (gating)

A change being on `firefox-main` does not mean users will see it. This is the step most likely to
produce a wrong release note, and the two highest-profile features of a cycle have both turned out
to be fully off on every channel before now — including Nightly.

**Every lookup in this file must read from `origin/main`, never the working tree.** Use
`git show origin/main:<path>`. The working tree is routinely on another branch or days behind, and
will report pre-landing defaults. See the freshness rules in
[`bugzilla-access.md`](bugzilla-access.md).

Sort every candidate into one of three states:

- **Live** — reaches users as Firefox N rides the trains.
- **Gated** — landed but hidden (preference off, Nimbus-controlled, platform-limited, Labs opt-in).
- **Landed but not usable yet** — the code is in but a web author or user cannot exercise it
  (parsing-only or phase-1 platform work). Not a note; track it and note it when it actually ships.
  This is why JPEG XL only appeared once it was offered in Firefox Labs.

## The three preference files

| File | Role |
|---|---|
| `modules/libpref/init/StaticPrefList.yaml` | Gecko-wide static prefs (~2,710 entries) |
| `browser/app/profile/firefox.js` | **Desktop** Firefox defaults and overrides (~174 preprocessor conditionals) |
| `mobile/android/app/geckoview-prefs.js` | **Android** defaults and overrides (145 prefs), shipped with GeckoView and Fenix |

The app-level file overrides a StaticPrefList default **for the product that ships it**, so check
StaticPrefList plus the one belonging to the platform in question — and never let the desktop file
speak for Android or vice versa.

### How a feature ships to Android specifically

**Trigger: the candidate is in a `Firefox for Android` or Fenix component.** Not "the preference
looks wrong" — a Fenix-level feature frequently has *no Gecko preference at all*, so finding no
preference is not evidence the feature is ungated, and there is no puzzle to prompt the check. Bug
2054954 ("Add timeline to media notification") was proposed as a plain `New` note for 155 with no
gating verdict of any kind, because a search for a Gecko pref came back empty and the Nimbus FML was
never opened.

Three mechanisms, in this order:

1. **`geckoview-prefs.js` override.** 40 of its 145 prefs override a StaticPrefList default and 60
   exist nowhere else, so reading only StaticPrefList gives the wrong answer for Android on 100
   preferences. `apz.drag.enabled` is `true` in StaticPrefList and `false` here; `browser.fixup.
   domainwhitelist.localhost` exists only here.
2. **An `#ifdef ANDROID` / `@IS_ANDROID@` default** inside StaticPrefList — 271 preferences currently
   resolve differently on Android than on Windows for this reason.
3. **The Nimbus FML**, `mobile/android/fenix/app/nimbus.fml.yaml`, for Fenix-level features that have
   no Gecko pref at all. A feature can be hardcoded `false` there while every Gecko pref looks
   enabled (bug 2047027, Android tab groups). **Read the per-channel block, not just whether the
   feature appears** — the answer is usually a channel split rather than on/off, and the split
   decides the note:

   ```yaml
   media-notification-improvements:      # bug 2054954
     - channel: developer  -> enabled: true
     - channel: nightly    -> enabled: true
     - channel: beta       -> enabled: true
     - channel: release    -> enabled: false
   ```

   Live on Nightly and Beta, off on Release. That is not "hold until it ships" — it is a `nightly+`
   candidate now, with the mainline note waiting for the release flip. Reporting it as a plain `New`
   note for the current version would have been wrong in both directions.

Worked example: `security.pki.certificate_transparency.mode` carried a platform/channel condition in
StaticPrefList while Certificate Transparency had already shipped to Android release separately — see
bug 2057694 comment 5, pointing at bug 1990316. Reading the shared default alone would have said
Android was not covered.

`pref-delta.py` reads all three files and resolves Android by default; `--platforms` narrowing is
reported in its output so a desktop-only run is never silently mistaken for a complete one.

## Reading a StaticPrefList default correctly

An entry looks like:

```yaml
- name: some.feature.enabled
  type: bool
  value: false
  mirror: always
```

Three traps:

1. **`@DEFINE@` indirection.** 131 entries don't hold a literal — they hold a token:

   ```yaml
   value: @IS_NIGHTLY_BUILD@
   ```

   These are resolved by `#define` blocks at the top of the file (`#ifdef NIGHTLY_BUILD` →
   `#define IS_NIGHTLY_BUILD true` / `#else` → `false`). Most common: `@IS_NIGHTLY_BUILD@` (60),
   `@IS_ANDROID@` (19), `@IS_EARLY_BETA_OR_EARLIER@` (12), plus `IS_NOT_*` inversions. **A parser
   that reports the literal `@IS_NIGHTLY_BUILD@` as the default has told you nothing** — resolve
   the define per channel.

2. **Whole entries wrapped in `#ifdef`.** A pref can be *absent* on some channels:

   ```yaml
   #ifdef NIGHTLY_BUILD
   - name: gfx.webrender.panic-on-gl-error
     ...
   #endif
   ```

   Absent is different from false, though for note purposes both mean "not reaching those users."

3. **`mirror: once` vs `mirror: always`.** `once` is read at startup only — a flip needs a restart.
   Rarely matters for the note text, occasionally matters for a Labs note's instructions.

## Channel semantics

| Guard | True on |
|---|---|
| `NIGHTLY_BUILD` | Nightly only |
| `EARLY_BETA_OR_EARLIER` | Nightly and the early part of Beta |
| `RELEASE_OR_BETA` | Beta and Release (i.e. not Nightly) |
| `MOZ_DEV_EDITION` | Developer Edition |
| `MOZILLA_OFFICIAL` | Official builds only (not local) |
| `XP_WIN` / `XP_MACOSX` / `MOZ_WIDGET_GTK` / `ANDROID` | Per platform |

State the answer plainly per channel — "off by default on all channels", "Nightly-only",
"on by default everywhere" — because that is what decides whether a note ships now or is held.

## Preference flips: diff the endpoints, never replay commits

**The flip is often the whole story.** A feature's code can land months before it becomes live; the
commit that makes it noteworthy is a one-line default change with an unremarkable subject, invisible
to any funnel that filters on commit messages.

Find flips by diffing the **window's endpoints**:

```
git diff <START>..<END> origin/main -- modules/libpref/init/StaticPrefList.yaml \
    browser/app/profile/firefox.js mobile/android/app/geckoview-prefs.js
```

**All three files, or the diff is desktop-only.** Omitting `geckoview-prefs.js` is how an
Android-only flip goes unseen — it is where 100 of Android's real defaults live (see above).

Do **not** replay commit-by-commit. Land → backout → re-land churn inside a single window makes
per-commit replay actively wrong; an endpoint diff collapses it for free. A real example from one
7-day window: `browser.nova.enabled` was flipped, reverted for mochitest failures, then re-landed —
three commits, one net change.

Subjects worth grepping for once you have candidates: `Flip`, `Enable ... by default`,
`unconditionally`, `Set ... false`, `disable`.

## Nimbus experiments and rollouts

Code being in and the preference being on still isn't enough if delivery is Nimbus-controlled.
Check `toolkit/components/nimbus/FeatureManifest.yaml` for a feature id, and the bug or its
whiteboard for a rollout. New Tab widgets, onboarding/messaging (OMC), and many Android features are
Nimbus-gated even with the code fully landed.

A Nimbus-gated feature is **not** automatically disqualified — 36 notes in the last two years are
marked as progressive rollouts. It changes the *wording* (and sometimes adds a country/population
qualifier), and it means the release-note decision belongs to whoever owns the rollout schedule.

## Firefox Labs

An experimental feature shipping preference-off and enabled from the Firefox Labs panel in Settings
maps to the **Firefox Labs** note category, and *does* get a live note. See
[`style-guide.md`](style-guide.md) for the front-loading requirement — the experimental,
off-by-default nature must be in the first sentence.

## Platform gates

`#if defined(XP_WIN)` in the patch, or a fix confined to `widget/cocoa`, means the note needs a
platform qualifier. But confirm it from the **changed file paths**, not the bug's component or
summary — see the scoping section of [`bugzilla-access.md`](bugzilla-access.md). Narrow platform
reach does not disqualify a note: several shipped `Fixed` notes are macOS- or Windows-only.

## Web-platform features: shipped vs. parsing-only

For a CSS property, DOM API, or JS feature, "landed" frequently means the parser accepts it while
nothing is implemented behind it. A note requires that a **web author can actually use it now**.
Check the pref default *and* whether the implementation is behind a separate flag; when in doubt,
treat it as "landed but not usable yet" and put it on the watchlist.

## Gotchas worth repeating

- **No bug carries the `relnote-firefox` flag on fresh landings.** That flagging is the manual work
  discovery bootstraps, so classification is heuristic (component + type + subject + what the patch
  does), not flag-driven.
- **`cf_status_firefox{N} = disabled` still means landed** — it signals the fix sits behind an
  off-by-default preference. Keep the bug and resolve the gating; don't drop it.
- **Don't assume a big feature is live because its code landed.** Confirm the default. Every
  subagent that read an on-disk pref file has gotten this wrong; only `git show origin/main:...`
  was correct.
