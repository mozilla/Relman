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

## Which preference is it?

Everything below assumes you already hold the preference's **name**, and getting it wrong is not a
near miss. `pref-delta.py --lookup` can only answer about the string you hand it, and a review pass
read `NOT FOUND` as *this feature has no gate* and reported a Nightly-only feature's gating as
undetermined.

**Never rebuild the name from prose.** A note reading "two new keywords, `closest-corner` and
`farthest-corner`, for the radial size in `ellipse()`" does not tell you the preference is
`layout.css.ellipse-corners.enabled`. Three names constructed from note wording in one review
(`layout.css.basic-shape-corner-keywords.enabled`, `layout.css.alpha-function.enabled`,
`layout.css.relative-color-syntax.alpha.enabled`) were all absent from the tree. Read it instead:

1. **For a web-platform surface, grep `dom/webidl` for the API name the note already gives you.**
   The `[Pref=…]` annotation names the gate outright, and it is the only method that works when the
   gate is older than the change:

   ```
   git -C <clone> grep -n 'StylePropertyMap' origin/main -- 'dom/webidl/*.webidl'
   ```

   ```
   CSSStyleRule.webidl:50: [SameObject, Pref="layout.css.typed-om.enabled"] ... styleMap;
   Element.webidl:459:     [SameObject, Pref="layout.css.typed-om.enabled"] ... attributeStyleMap;
   ```

   All 152 preferences named this way are also declared in StaticPrefList, so this is a *reverse
   lookup* — API name to preference — not a second place a gate hides. It is the fastest route for a
   CSS/DOM/JS note because the note names the interface.

   **The annotation sits on the entry points, not on the interface**: `StylePropertyMap.webidl`
   itself carries none, and the gate is on `Element.attributeStyleMap` and `CSSStyleRule.styleMap`.
   Grep the API name across the whole directory rather than opening the file that shares its name.

   **`[Func=…]` names a C++ predicate, not a preference, and no lookup can resolve it** — 272 of
   these, against 579 `[Pref=…]`. `ONNX.webidl`'s `Func="InferenceSession::InInferenceProcess"`
   exposes the interface only inside Firefox's inference process, so it is invisible to web content
   however the preferences read. Read the predicate.
2. **Grep the shortest distinctive token** in StaticPrefList:

   ```
   git -C <clone> grep -n 'corner' origin/main -- modules/libpref/init/StaticPrefList.yaml
   ```

   Seven lines, one of them `# Is support for closest/farthest-corner enabled in ellipse()?`
   immediately above the entry. `corner` succeeds where `corner-keywords` and `basic-shape` both
   failed, because a one-word grep cannot be wrong about word order or plurals. The same pass ran
   two greps on this exact file and missed, both times by pattern-matching a guessed *name*.

   **Take the token from the feature area, not from the API being added.** `stylepropertymap`
   matches nothing in that file; `typed`, from the note's own "CSS Typed Object Model", matches 10
   lines including `layout.css.typed-om.enabled`. If the obvious noun returns nothing, try the
   broader one before concluding anything.
3. **Read the patch** — the surer route when the note's wording shares no word with the preference.
   With a window, `bug-detail.py <bug> --landings A..B`. Without one:

   ```
   git -C <clone> log --format='%H %s' --grep='Bug 2045278' origin/main
   git -C <clone> show <sha> -- modules/libpref/init/StaticPrefList.yaml
   ```

   **List every landing; do not take the newest.** `log -1` on bug 2057406 returns "Annotate another
   passing test", not the implementation. Phabricator serves the same patches with no clone at all —
   see [`bugzilla-access.md`](bugzilla-access.md).

**A patch that touches no preference file is not evidence of no gate.** This is the failure the
methods above are ordered to prevent, and the one that shipped a wrong note. Bug 2057406 added
`StylePropertyMap.delete()` across `nsDOMCSSDeclaration.h`, `StylePropertyMap.cpp`,
`StylePropertyMapReadOnly.h`, `glue.rs` and WPT metadata — **no preference file at all** — because
the interface had been gated behind `layout.css.typed-om.enabled` (`@IS_NIGHTLY_BUILD@`,
Nightly-only) long before. Reading the patch answers *no gate in this change*, a different claim
from *no gate*: the gate belongs to the surface a change extends, not to the change. Adding a method
to an existing interface is where the two come apart, and that is common in web-platform work.

**A lookup now distinguishes three outcomes; treat them differently.** `NOT FOUND` plus a list of
nearest existing names means the name is wrong — take one of the suggestions. `NOT FOUND` with no
suggestions means no name in the tree shares a distinctive token with it, so go back to the patch.
**`PRESENT IN THE TREE BUT NOT RESOLVABLE`** means the entry is there but no default was computed
for it, and the output names which of three reasons applies. Only the first is a tool limit:

- **A symbol in its guard is unmodelled**, so the block was treated as inactive and the preference
  dropped out. The message names the symbol. `print.experimental.skpdf` is the standing example —
  `@IS_NIGHTLY_BUILD@` inside `#ifdef MOZ_ENABLE_SKIA_PDF` — and this is the case where reading
  the matching `moz.configure` option is the next step.
- **Its guard is false for every configuration you asked about**, correctly evaluated. `#ifdef
  ANDROID` with `--platforms win` does this to 88 entries. Widen the configurations; there is
  nothing to look up.
- **No configuration you asked about reads the file it lives in** — what a narrowed `--platforms`
  does to the preferences that exist only in `geckoview-prefs.js` (see the Android section below).

The file, line, guard and every `value:` line in the entry are quoted so you can finish the reading
by hand, and none of the three is evidence of no gate. When an entry holds more than one `value:` —
190 of them do, because the default itself sits in an `#if` — the output says so rather than picking
one.

## The three preference files

| File | Role |
|---|---|
| `modules/libpref/init/StaticPrefList.yaml` | Gecko-wide static prefs (2,885 entries) |
| `browser/app/profile/firefox.js` | **Desktop** Firefox defaults and overrides (70 preprocessor conditionals) |
| `mobile/android/app/geckoview-prefs.js` | **Android** defaults and overrides (145 prefs), shipped with GeckoView and Fenix |

**Every preference count in this file was re-measured at `origin/main` on 2026-08-13**, and the
`dom/webidl` annotation counts on 2026-08-14; each is reproducible with a grep. The shipped-note
counts further down are not covered by that. Most had drifted or were never reproducible, so if one
looks wrong, re-measure it rather than reasoning from it. The entry count is `- name:` lines, of
which 173 are written `-   name:`; match them with `pref-delta.py`'s `YAML_NAME_RE` rather than a
stricter pattern of your own.

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

1. **`geckoview-prefs.js` override.** Read only StaticPrefList and **all 145 of its preferences give
   the wrong answer on Android**, in two different ways. **40 also exist in StaticPrefList, and all
   40 set a different value** — `apz.drag.enabled` is `true` there and `false` here. The other **105
   are absent from StaticPrefList entirely**, so it reports them as not existing at all: 43 of those
   are declared in no other preference file either (`media.mediadrm-widevinecdm.visible`), and the
   remaining 62 are declared in the *desktop* files, which Android never reads. The file contains no
   `#ifdef`, so these are plain declaration counts.
2. **An `#ifdef ANDROID` / `@IS_ANDROID@` default** inside StaticPrefList — 114 entries carry an
   Android-conditional default (`@IS_ANDROID@`, `MOZ_WIDGET_ANDROID`, or a bare `ANDROID`), and 85
   of them do resolve differently on Android than on Windows on Nightly. Grep all three spellings:
   `\bANDROID\b` alone misses `MOZ_WIDGET_ANDROID`, because the underscore is a word character.
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

1. **`@DEFINE@` indirection.** 134 entries don't hold a literal — they hold a token:

   ```yaml
   value: @IS_NIGHTLY_BUILD@
   ```

   These are resolved by `#define` blocks at the top of the file (`#ifdef NIGHTLY_BUILD` →
   `#define IS_NIGHTLY_BUILD true` / `#else` → `false`). Most common: `@IS_NIGHTLY_BUILD@` (64),
   `@IS_ANDROID@` (19), `@IS_EARLY_BETA_OR_EARLIER@` (11), plus `IS_NOT_*` inversions
   (`@IS_NOT_ANDROID@` 10, `@IS_NOT_MOBILE@` 9, `@IS_NOT_NIGHTLY_BUILD@` 6). **A parser
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
Android-only flip goes unseen — it is where 145 of Android's real defaults live, 105 of them found
in no other file Android reads (see above).

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

**`XP_LINUX` is true on Android.** It is `set_define("XP_LINUX", target_has_linux_kernel)` in
`build/moz.configure/init.configure`, so `#ifdef XP_LINUX` covers desktop Linux *and* Android —
which is why over 20 files write `defined(XP_LINUX) && !defined(ANDROID)` when they mean desktop
Linux alone. Reading such a guard as desktop-only inverts the Android answer on 14 preferences.

[Platform-specific build defines](https://wiki.mozilla.org/Platform/Platform-specific_build_defines)
is the per-platform table for `XP_*` and `MOZ_WIDGET_*`, and it is worth opening before reasoning
about any guard. `init.configure` wins where the two disagree: the wiki's prefs-file table still
lists `mobile/android/app/mobile.js`, which no longer exists in the tree.

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
