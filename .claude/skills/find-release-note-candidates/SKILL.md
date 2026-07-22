---
name: find-release-note-candidates
description: Scan everything that landed on mozilla-central (the Nightly train) in a recent window and propose release-note candidates — with bug number, whether the change is gated (and how), and a draft one-liner in the Release Management style. Use when the user wants to find release notes for recent landings, work the daily "what landed overnight" pass, or asks things like "what should we note from the last day", "find release-note candidates", "anything user-facing land this week". Complements review-release-notes (which critiques an existing draft); this one produces the candidate list from scratch.
---

# Find Release Note Candidates

Go through the commits that landed on `mozilla-central` (the Nightly train) in a recent window, and surface the ones worth a release note. For each surviving candidate, produce: the **bug number(s)**, whether it is **live or gated** (and the exact gating mechanism), a suggested **category**, a **draft one-liner** in the Release Management style, and a **screenshot suggestion** where a visual would help.

**Scope: propose only.** This skill reads the tree and Bugzilla and produces a candidate list. It does **not** set the `relnote-firefox` flag, comment on bugs, nominate anything, or publish. Those are follow-up actions the user takes afterward. When you've drafted a one-liner or a gating call, present it for the user to apply; never write to Bugzilla or to any release-notes system from this skill.

This is the mirror image of `review-release-notes`: that skill critiques an already-written draft; this one finds what should be in the draft. Defer to it for the full style rules (summarized below) and reuse its bug/patch-lookup conventions.

## Channel mapping and freshness (get this right first)

The version-to-channel mapping shifts about monthly; never assume it. Fetch it live:

```
curl -s https://product-details.mozilla.org/1.0/firefox_versions.json
```

`FIREFOX_NIGHTLY` (e.g. `154.0a1`) is what lands on `mozilla-central` today — call it **N**. `LATEST_FIREFOX_DEVEL_VERSION` is Beta (**N-1**), `LATEST_FIREFOX_VERSION` is Release (**N-2**), `FIREFOX_ESR` is ESR. A change that lands now ships to users in **Firefox N**, so its Bugzilla status field is `cf_status_firefox{N}`.

Two freshness rules that the trial proved matter:

- **Refresh the mirror before logging.** Run `git fetch origin main` in the Gecko checkout first; a stale local `main` will miss the most recent day. The working tree is often checked out on `autoland` or an older commit — do not trust files on disk.
- **Read code and preference defaults from `origin/main`, not the working tree.** To check a preference's default, use `git show origin/main:modules/libpref/init/StaticPrefList.yaml` (or `browser/app/profile/firefox.js`), never the on-disk copy — it can be days behind central and will show the wrong (pre-landing) default. This is the single most common way to get a gating call wrong.

## Step 1 — enumerate the window

Default window is **the last 24 hours** (roughly the last two Nightly builds); accept a different window if the user asks (the trial used 96h). List the landings:

```
git log --since="24 hours ago" --format="%H%x09%s" origin/main
```

Expect a few hundred commits per day. Most are not note-worthy — the funnel below cuts them down.

## Step 2 — bug numbers and backouts

Extract the primary bug id from each subject (`Bug NNNNNNN - ...`; take the first bug id in the subject as the primary).

**Detect backouts.** On this git mirror, backouts are phrased `Revert "..."` (git style), **not** the hg-style "Backed out" — matching only "Backed out" finds zero. Match subjects starting with `Revert` (also allow "Backed out"/"backout" for safety). Collect the reverted bug ids, but do **not** rely on the revert text to decide what's dead: things land, get backed out, and **re-land** within the same window. The authoritative signal is Bugzilla status (next step). Note which bugs appeared in a revert so you can double-check their final state.

## Step 3 — pull status and metadata from Bugzilla (REST for enumeration)

The `moz` MCP fetches a bug *by id* but has no search/buglist tool, and `buglist.cgi` is WAF-challenged through WebFetch. So enumerate lightweight fields through the REST API (the one acceptable non-MCP Bugzilla call, exactly as `review-release-notes` does for its query). Batch the ids (≈120 per call):

```
curl -s "https://bugzilla.mozilla.org/rest/bug?id=<comma,ids>&include_fields=id,summary,status,resolution,product,component,keywords,type,flags,whiteboard,cf_status_firefox<N>"
```

Some bugs won't come back — they're security-restricted; count and disclose them, don't chase them.

For the **detail** you need to judge a candidate (what actually changed, gating, suggested wording), read the full bug — prefer the MCP resource `@moz:bugzilla://bug/{id}`, and fall back to `curl -s https://bugzilla.mozilla.org/rest/bug/{id}` + `/comment` when the MCP redacts untrusted reporter content or isn't connected. Say which you used. For the patch, read `@moz:phabricator://revision/D{id}` (the `D` number is in the bug's commit comments).

## Step 4 — keep only what is currently FIXED (the real backout filter)

Keep a bug only if it is **`resolution=FIXED` and status `RESOLVED` or `VERIFIED`**, with `cf_status_firefox{N}` = `fixed`/`verified` (treat `disabled` as still-landed — it just means the fix lives behind an off-by-default preference; keep it and handle gating in Step 7). Everything that landed-then-backed-out and hasn't re-landed will sit at `REOPENED`/`NEW`/`ASSIGNED` and drops out here — this is what catches the "landed on main but backed out later" case the enumeration alone can't. Re-verify any bug that showed up in a revert: confirm its *final* state is FIXED before trusting it.

## Step 5 — drop the mechanical noise

Set aside, without deep analysis, the commits that are structurally never release notes: test-only changes (crashtest/reftest/wpt/mochitest/xpcshell, "add a test", "mark X passing/failing"), build-system/CI/lint, vendoring and library bumps, `BUG_COMPONENT` edits, documentation, string/locale-only landings, supply-chain audits, and pure internal refactors/cleanups (rename, remove-dead-code, move-to, deduplicate). Also set aside anything in internal components (Testing, Build System, Developer Infrastructure). Keep what touches user-facing components (Firefox/Toolkit/Fenix UI areas), web-platform components (Core DOM/CSS/Layout/JS/Media/Graphics/Networking/Storage/WebExtensions...), or Developer Tools.

## Step 6 — apply the significance bar (this does most of the filtering)

Calibrate against **shipped** notes, not "is it user-facing at all": read a recent example such as https://www.firefox.com/firefox/152.0/releasenotes/. The bar is high.

**Keep:** genuine user-facing features (new UI, a new capability, new languages/dictionaries), notable behavior changes users would actually notice (where files open, zoom increments), fixes to things users really hit (paste missing, image drag broken, right-to-left navigation), and **fully-shipped** web-platform capabilities a web author can use now (e.g. `field-sizing`, WebAuthn Related Origin, `text-box`).

**Drop as too minor:** internal performance micro-optimizations (even if benchmarked), minor visual polish (spacing, a single icon swap, a full-height tweak, a stray "New" label), niche developer-API removals, and correctness fixes only visible in edge cases. **Parsing-only / phase-1 web-platform work that isn't author-usable yet is not a note** — record it in the "landed but not usable yet" watchlist (Step 9) and note it when the property actually ships, the way JPEG XL only appeared once it was offered in Firefox Labs.

When you drop something, keep a one-line reason — the output shows the dropped list so the user can rescue anything (per their preference).

## Step 7 — roll up features, and determine gating

**Roll up per feature.** Work fans out across many bugs (a New Tab widget can be 6-7 bugs; the "Nova" desktop refresh is 15+ theming bugs). Produce **one** candidate per feature, listing every contributing bug number — not one note per bug. Treat the whole **Nova** desktop-interface refresh as a single "Changed" rollup.

**Determine live vs gated, with the mechanism.** For each candidate, decide whether it reaches users as Firefox N rides the trains, or is hidden. Check, reading from `origin/main` (Step's freshness rule):

- **Preference gate** — grep `StaticPrefList.yaml` and `browser/app/profile/firefox.js` for the controlling preference and read its actual default and any per-channel/`#ifdef NIGHTLY_BUILD` value. State it plainly (e.g. "off by default on all channels", "Nightly-only", "on by default everywhere").
- **Nimbus experiment / rollout** — check `toolkit/components/nimbus/FeatureManifest.yaml` and the bug/whiteboard for a feature id or rollout. New Tab widgets, onboarding/messaging (OMC), and many Android features are Nimbus-gated even when the code is in.
- **Firefox Labs opt-in** — an experimental feature that ships preference-off and is enabled from the Firefox Labs panel in Settings. This maps to the **Firefox Labs** note category (see Step 8).
- **Platform gate** — e.g. Taskbar Tabs is Windows-only (`#if defined(XP_WIN)`); a macOS-only fix.

Sort the output into **live now**, **gated (landed but hidden)**, and **landed-but-not-usable-yet (track for future)**. A fully-experimental opt-in belongs in the Firefox Labs category with a live note; a feature that is simply off on every channel (like Nova or an unshipped Nimbus rollout) goes in the gated bucket and is flagged for the user to decide whether to hold.

## Step 8 — draft the one-liner and category

Categories: **New** (new features), **Fixed** (resolved bugs), **Changed** (interface/behavior changes), **Developer** / **Web Platform** (developer- or web-platform-facing), **HTML5** (legacy tag for web-platform — match what the product uses), **Firefox Labs** (experimental opt-in; front-load the experimental/off-by-default nature and say how to enable it).

Write one line per candidate, following the Release Management style guide (https://wiki.mozilla.org/Release_Management/Release_Notes#Release_Notes_Style_Guide) — the same rules `review-release-notes` enforces:

- Plain language for a broad, international audience; focus on **user impact**, not internals. (Web Platform / Developer notes may be technical and reference APIs.)
- Spell terms out — "preference" not "pref", "Developer Tools" not "DevTools"; avoid abbreviations. Do **not** mention `about:config` preference names in the note text.
- **Fixed** notes lead with a past-tense verb ("Fixed", "Improved", "Removed"). **New**/**Changed** notes are present-tense descriptive ("X now does Y").
- End every note with a full stop. De-localize any URLs.
- Don't put bug links in the note text (mainline convention) — but always carry the bug number(s) alongside the candidate in this skill's output, since that's what the user needs to act.

Keep the draft copy-pasteable; the user will edit and approve.

## Step 9 — screenshots

Suggest a screenshot when a visual would materially help the note: new or restyled visible UI (widgets, menus, onboarding, the Nova refresh, a new toolbar control). Say no for behavior/platform/back-end changes with nothing new to see (most Fixed, Web Platform, and Developer notes). The style guide has no hard rule here — this is a judgment call, so briefly say why.

## Execution model (adaptive)

- **Small window / few survivors:** do the deep-dive inline, one candidate at a time.
- **Larger set (a normal daily run easily reaches dozens post-Step-5):** fan out parallel subagents grouped by area (New Tab/Messaging, Address Bar/Search, CSS/Layout, JS/Networking/Media, Android/Autofill, and the big desktop features + Nova rollup as its own batch). Give every subagent the freshness rules (read prefs from `origin/main`), the significance bar, and the requirement to verify final FIXED state. Then synthesize into one report.

## Output

Lead with a one-line funnel (commits → FIXED → candidates → survivors) and the query window + Nightly version. Then:

1. **Live candidates that clear the bar** — a scannable table: category · bug(s) · one-liner · screenshot?
2. **Gated** (landed but hidden) — same columns plus the gating mechanism; flag which the user must decide to hold.
3. **Landed but not usable yet** — brief watchlist to note when it ships.
4. **Dropped (too minor / noise)** — collapsed, one line each with the reason, so nothing is silently discarded.

Close with a short methodology note: the exact window, the Nightly version and channel mapping used, that the backout filter is Bugzilla-FIXED (with any land-then-reland cases called out), that preference defaults were read from `origin/main`, which lookups used the MCP vs REST, any security-restricted bugs skipped, and anything you could not determine (and why). Keep it a working queue, not an essay.

## Gotchas learned from the trial

- **No bug carries the `relnote-firefox` flag yet** on fresh landings — that flagging is the manual work this skill bootstraps, so classification is heuristic (component + type + subject + what the patch does), not flag-driven.
- **The two highest-profile items can be fully off** — in the trial, both the Nova refresh and Smart Window were preference-off on *every* channel including Nightly, so nothing there was user-visible. Always confirm the default; don't assume a big feature is live because its code landed.
- **The working tree lies about preference defaults.** Every subagent that read the on-disk file got a stale value; only `git show origin/main:...` was correct.
