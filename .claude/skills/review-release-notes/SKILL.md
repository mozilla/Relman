---
name: review-release-notes
description: Review draft Firefox release notes for style, tone, scoping, and audience-fit against the Mozilla Release Management Release Notes Style Guide. Use when the user wants release notes checked/edited before publishing — they may paste the notes, point to an exported file, or give a published-to-web Google Doc URL. Triggers on requests like "review these release notes", "check my relnotes draft", "do these notes follow the style guide".
---

# Review Release Notes

Review draft Firefox release notes against the Mozilla Release Management style guide, checking for consistent style, correct audience scoping, and proper categorization. Produce a clear, actionable critique with concrete suggested rewrites.

## Getting the draft

The notes can come in three ways. Direct Google Drive access is **not** available in this environment (no Drive/Docs integration), so:

1. **Pasted text or an exported file** — the most reliable. Ask the user to paste the notes, or read an exported copy (`.txt`, `.md`, `.docx`, `.html`) they point you to with the Read tool.
2. **A "Published to the web" Google Doc** — if the user did File → Share → Publish to web, the resulting public `docs.google.com/.../pub?...` URL can be read with WebFetch. A normal Drive share link (`/edit`, `/view`) is authenticated and will fail — ask them to publish to web or export instead.
3. If you only have a private share link, tell the user it can't be opened directly and ask for a paste, export, or published-to-web URL.

**Dot releases come as a staging URL, not a Google Doc.** For a dot release (e.g. 151.0.4), the notes are generally provided as a rendered staging page on the dev server rather than a draft doc — e.g. `https://www-dev.springfield.moz.works/en-US/firefox/151.0.4/releasenotes/`. Read it with WebFetch like any public page. A couple of things follow from this:
- It's the *rendered* page, so it already reflects the final layout/tags — review it as published output. The publish-lag/cache caveat below still applies when the author re-stages after edits (bust the WebFetch cache with a throwaway query param).
- Remember dot releases **require bug links** (the opposite of mainline), so expect and check linked bugs rather than flagging them.

**Publish lag when re-checking edits.** The published `/pub` snapshot is regenerated on a delay (the doc header usually says "Updated automatically every 5 minutes"), so right after the author makes edits it can trail their live document by a few minutes. On top of that, WebFetch caches each URL for ~15 minutes, so re-fetching the *same* URL can return your own earlier (pre-edit) copy. So when you re-check whether requested changes were applied and the old text is still showing:
- **Bust the WebFetch cache** by appending a throwaway query param (e.g. `…/pub?freshness=recheck2`) so it's treated as a new URL and actually re-fetched.
- If the change *still* isn't there after a genuinely fresh fetch, it's most likely the `/pub` publish lag, not a missed edit — don't tell the author they forgot. Say you're seeing a stale published snapshot, wait a few minutes, and re-check before concluding anything.

Before reviewing, confirm the **target**: which product (Firefox Desktop, Firefox for Android/iOS, Focus), which channel/version, and whether these are mainline, dot-release, beta, ESR/Enterprise, or known-issues notes. Audience scoping depends on it (see below).

## Source of truth

The authoritative guide is the wiki — consult it when a question isn't covered below or when you suspect the rules have changed:
- Style guide: https://wiki.mozilla.org/Release_Management/Release_Notes#Release_Notes_Style_Guide
- Published examples to model tone against: https://www.firefox.com/en-US/releases/ (individual versions at `/firefox/{VERSION}/releasenotes/`)

The rules below are a working summary; the wiki wins on any conflict.

## Looking up bug and patch context

Drafts usually don't include bug numbers, but the author may reference a bug (or two notes that came from related bugs) and ask whether they should be consolidated, recategorized, or rescoped. To answer well you often need to know what the change actually does. Pull that context from the Mozilla MCP server (`moz`):

- **Bug:** read the MCP resource `@moz:bugzilla://bug/{bug_id}` (e.g. `@moz:bugzilla://bug/1138419`) for the summary, component, and status.
- **Phabricator revision:** read `@moz:phabricator://revision/D{revision_id}` for the patch and its review comments. Find the revision ID in the commit log (`jj log -T builtin_log_detailed`, or `git log -v` in a Git checkout).

Use this to confirm scoping (is it really user-facing?), to verify two notes describe the **same** underlying change before recommending a merge, and to ground rewrites in what shipped.

**Caution on platform scoping:** a bug's component (e.g. `Widget: Cocoa`) or summary may name one OS while the actual fix is platform-agnostic — the underlying cause can be OS-specific even when the corrected behavior, the patch, or the reviewers aren't. Don't narrow a note to the component's platform on that basis.

The **definitive** check is the patch itself: read it and look at the paths of the changed files. Changes under platform-specific directories (`widget/cocoa`, `widget/gtk`, `widget/windows`, `*/mac`, `*/gtk`, `*/win`) are genuinely that-OS-only; changes under shared directories (`browser/`, `toolkit/`, `dom/`, `layout/`, `gfx/`) ship to every platform regardless of which OS the bug was reported on. Get the diff from:
- **Phabricator (always available, use this by default)** — `@moz:phabricator://revision/D{revision_id}`. The `D` number is in the bug's commit comments, or in the `Differential Revision:` line of the Git commit message.
- **A local Gecko checkout, only if one exists** — don't assume there is one or guess its path (it varies per user, and many people running this skill won't have one at all). If you know a checkout is present, `git -C <firefox-checkout> show --stat <commit_hash>` (commit hash is in the bug's pulsebot "Pushed by" comment) lists touched files; drop `--stat` for the full diff. Otherwise just use Phabricator.

Only when you genuinely can't read the diff, fall back to weighing the developer's suggested relnote wording (often deliberately generic) and reviewers; prefer the developer's intended scope and keep the note generic rather than adding a platform qualifier that might be wrong — raise it as a question instead.

To make these lookups possible without guesswork, **recommend the author provide a link to a Bugzilla query listing every bug flagged for the release's notes** (i.e. the bugs with the relnote tracking flag set for this version). With that query in hand you can map each note to its bug, spot duplicates and miscategorizations, and quote the suggested relnote wording the developer left on the bug. The query is per-release, so the link differs every time — ask the author for the current one rather than reusing a previous link. (An illustrative example of the shape: https://mzl.la/4okcqmf)

### Enumerating the query into bugs

The `moz` MCP fetches a bug *by ID* but has no search/buglist tool, and the Bugzilla buglist HTML page won't render through WebFetch (it returns a JS "required part of this site couldn't load" error). So turn the query link into a list of bugs in three steps:

1. **Get the query parameters.** The link may already be a direct `bugzilla.mozilla.org/buglist.cgi?...` URL — if so, the search params are right there (e.g. `f1=cf_tracking_firefox_relnote&o1=equals&v1=152%2B`); read them off and skip to step 2. If it's a shortlink (e.g. `mzl.la`), WebFetch it: WebFetch won't follow the cross-host redirect, but it reports the destination `buglist.cgi?...` URL, which carries the same params.
2. **Hit the REST API for the ID list.** Rebuild those params against the JSON endpoint, which needs no browser: `https://bugzilla.mozilla.org/rest/bug?cf_tracking_firefox_relnote=152%2B&include_fields=id,summary,component`. That returns every matching bug's id, summary, and component. (This read-only enumeration is the one acceptable non-MCP Bugzilla call — it exists only because the MCP can't search. Don't use REST to pull individual bug *detail*; use the MCP for that.)
3. **Fan out through the MCP.** For each note whose scoping, categorization, or consolidation you need to judge, read `@moz:bugzilla://bug/{id}` for the full picture. With the summaries from step 2 you can usually map most notes to bugs first and only deep-read the ones in question.

A bonus of the full list: you can cross-check **coverage** — flag any relnote-flagged bug with no matching note (a missing note) and any note that maps to no flagged bug (possibly mis-scoped or needs a nomination).

If the `moz` MCP server isn't connected in this session, the `@moz:` resources won't resolve — say so and ask the author for the bug summary (or which notes map to which bugs) rather than guessing. Beyond the query-enumeration REST call above, don't scrape Bugzilla through other means.

## Style rules to check

**Audience & focus**
- Write for a broad, international, non-technical audience. Avoid technical jargon and colloquialisms that don't translate well.
- For new or changed features, focus on **how it affects the user's experience**, not what the software is doing internally.
- Avoid abbreviations and shortenings — spell terms out in full. E.g. "preference", not "pref"; "Developer Tools", not "DevTools". This applies even in the Developer/Web Platform sections.
- Don't mention `about:config` preferences.

**Wording & grammar**
- **Fixed** notes start with a **past-tense verb**: "Fixed", "Removed", "Improved", "Updated".
- **New** and **Changed** notes are usually **present-tense descriptive** — "X now does Y" ("Geolocation on Windows now respects the user's location permission…"). Don't flag a present-tense Changed/New note for not starting with a past-tense verb; that's the expected register. Only the **Fixed** section needs the past-tense verb lead.
- End **every** note with a full stop (period) — including short ones.
- Defer to MDN's writing conventions for capitalization, contractions, numbers/numerals, pluralization, apostrophes & quotation marks, commas, hyphens, and spelling.

**Links**
- De-localize all URLs — remove the `en-US/` (or other locale) segment.
- Don't link to bugs in finalized mainline notes. **Exception:** dot releases require bug links.

**Known issues**
- Focus on user impact. If a workaround exists, give clear, step-by-step instructions.

**Categorization (tags)** — every note should sit under the right tag:
- **New** — new features
- **Fixed** — resolved known issues / bugs
- **Changed** — interface or behavior modifications
- **Developer** / **Web Platform** — developer-facing or web-platform changes
- **HTML5** — web platform issues (legacy tag; "Web Platform" is the modern equivalent — match what the target product uses)
- **Community** — community contributor work (note: this section is generated elsewhere — see "Sections to skip" below)

**Sections to skip during review**
- **Enterprise** — generally boilerplate (just a link to the separate enterprise release notes). Ignore it unless it contains more than that link, in which case review the extra content normally.
- **Community contributions** — generated elsewhere, not hand-authored here. Disregard it entirely.
- **Any empty section** — the draft is an intermediate staging document; the notes are later transferred into another system, where empty sections simply don't carry over. Ignore empty sections entirely (e.g. **Uncategorized**, **Unresolved**, or any tag with no notes under it). Don't flag them as "clear before publishing" or otherwise comment on them.

## Mobile (Android & iOS)

For Firefox for Android and iOS, we use generic, reusable release notes unless the Product team supplies their own:
- **Generic notes** — these don't need review. Skip them.
- **Product-supplied (non-generic) notes** — a **light review only**. Product generally provides these in the form they want, without our input, so flag only clear errors (typos, broken/localized links, obviously wrong scoping) and otherwise leave the wording alone. Don't apply the full desktop style critique.

If it's unclear whether mobile notes are generic or Product-supplied, ask before reviewing.

## Real-world tone reference (Firefox 151)

Model phrasing on shipped notes. Examples of the expected register:
- New: "Private Browsing Mode now allows you to instantly clear all data from your current session without closing the entire window."
- New: "Local Firefox profile backups are now available on Linux in addition to Windows, and you can restore them across platforms."
- Fixed: "Fixed incorrect screen resolution reporting to websites in multi-monitor setups."
- Fixed: "Various security fixes." (standard catch-all)
- Changed: "Geolocation on Windows now respects the user's Windows location permission setting, instead of overriding it, when the user grants location permission to a page."
- Web Platform notes may reference APIs and use inline `code`/MDN links, since that audience is developers.

Note the contrast: user-facing notes ("New", "Fixed", "Changed") stay plain-language and impact-focused; "Web Platform"/"Developer" notes can be technical and link to MDN.

## Review process

1. Acquire the draft (above) and confirm the target product/channel/version.
2. Go through each note and check it against every applicable rule. Pay special attention to:
   - **Scoping:** is a developer-only change sitting in a user-facing tag (or vice versa)? Is anything too internal/technical to belong in user notes at all?
   - **Verb/tense and full stops.**
   - **Jargon, abbreviations, and `about:config` mentions.**
   - **Localized URLs and stray bug links** (allowed only for dot releases).
   - **Correct tag** for each entry.
3. Watch for issues the style rules don't enumerate but that matter: duplicated/overlapping notes, inconsistent capitalization of feature names, vague impact ("improved performance" with no specifics), and notes that bury the user benefit.

## Output

Produce a review with:
- A short summary (overall quality, biggest themes).
- A per-note list of issues. **Walk the notes in the same order they appear in the document, top to bottom (by section, then by note within each section)** — the author reviews with the doc open and works straight down it, so matching that order lets them apply comments in place without hunting. Don't reorder by severity or theme, and don't group all the "jargon" or all the "consolidation" items together. For each note give: the original text, what's wrong (cite the rule), and a **concrete suggested rewrite**.
- For a consolidation, raise it at the position of the **first** of the notes involved and name the other notes (and their positions) it merges with, so it still appears in reading order.
- A short cross-cutting section *after* the in-order walk for anything that genuinely spans the whole doc (a coverage check against the bug query, a pattern like missing full stops throughout, terminology consistency). Keep per-note issues in the walk, not here.
- Flag anything you're unsure about as a question rather than a hard correction — especially audience-scoping calls that depend on product/channel context. Keep these questions attached to their note in the walk; you may also restate them in a short list at the end.

Keep suggestions concrete and copy-pasteable so the author can apply them directly back into their draft.
