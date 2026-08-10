---
name: review-release-notes
description: Review draft Firefox release notes for style, tone, scoping, and audience-fit against the Mozilla Release Management Release Notes Style Guide. Use when the user wants release notes checked/edited before publishing — they may paste the notes, point to an exported file, give a published-to-web Google Doc URL, or — for a dot release — a staging URL such as www-dev.springfield.moz.works. Also cross-checks note coverage against the bugs flagged for a release, reporting flagged bugs with no note and notes whose bug was never flagged. Triggers on requests like "review these release notes", "check my relnotes draft", "do these notes follow the style guide", "is our note coverage complete for 155".
---

# Review Release Notes

Review draft Firefox release notes against the Release Management style guide, checking for
consistent style, correct audience scoping, and proper categorization. Produce a clear, actionable
critique with concrete suggested rewrites.

**Scope: review only.** Assess the notes and report findings — do not publish, schedule, push, or
otherwise ship them anywhere. When a review comes back clean, say the notes have no outstanding
issues, not that they're "ready to publish" or anything implying you'll act on them.

## References — read these, don't restate them

**Paths below are relative to the repository root, not this skill's directory.** From the repo root,
`reference/release-notes/style-guide.md` resolves; `.claude/skills/…/reference/…` does not exist.

| Topic | File |
|---|---|
| The style rules you are enforcing | `reference/release-notes/style-guide.md` |
| What actually ships, for scoping calls | `reference/release-notes/shipped-notes-survey.md` |
| Bug/patch lookup, REST vs MCP, platform scoping | `reference/release-notes/bugzilla-access.md` |
| Whether a feature is really live | `reference/release-notes/gating.md` |
| Command forms that don't trigger permission prompts | `reference/release-notes/command-forms.md` |

`style-guide.md` is the authoritative working summary (the wiki wins on conflict) and covers
audience, tense, full stops, links, tags including **Firefox Labs**, sections to skip, mobile rules,
and the two recurring wording traps. Read it before reviewing rather than working from memory.

Use `shipped-notes-survey.md` when a scoping or categorization call is genuinely uncertain — it has
real shipped notes grouped by tag and sorted by length, which settles "is this the right register?"
faster than argument. It also shows that `HTML5` is still in live use and that the `Fixed`-in-majors
bar is moving, so don't flag either as anomalous.

## First run on a machine

If any script exits with **`error: could not locate the Gecko checkout`**, this machine has not been
set up. Ask where their Gecko clone is and run it once:

```
python3 scripts/relnotes/watchlist.py check-setup --repo <path to their clone>
```

That saves the path to per-user state for every script, and reports the `Bash(git -C <clone> …)`
permission entries the gecko reads need. Show those entries and ask before running `--write`, which
merges them into the git-ignored `.claude/settings.local.json` — it grants standing approval, so it is
the user's call. Never add them to the shared `settings.json`.

Reviewing rarely needs the clone (prefer Phabricator for patches, per `bugzilla-access.md`), so this
may not come up — but when it does, it is the whole reason a first pass feels like an approval
treadmill.

## Invoking commands

Follow `reference/release-notes/command-forms.md` — no `cd`, scratch files under `/tmp` by absolute
path, `curl -s "<url>"` with the URL first and flags after it, no shell loops. Written any other way,
better than half these commands stop for a permission prompt on a fresh checkout.

One rule specific to reviewing: **use WebFetch for a rendered page, and curl only when you need the
raw bytes.** WebFetch answers a prompt against the page rather than handing you the markup, so it will
paraphrase. Reach for curl whenever the review turns on exactly what is written — punctuation,
`href` targets, whether a note's wording is really what a summarizer reported. On this skill's last
run, WebFetch reported a note as `Improved Smart Window suggestions…` while the page actually rendered
a broken-markdown fragment, `Improved Smart Window](https://…) suggestions…`. Only the raw HTML showed
it, and it was the most serious finding of the review.

**Read the page with `note-page.py`, not with a hand-written regex.**

```
python3 scripts/relnotes/note-page.py <release-notes URL or a saved copy>
python3 scripts/relnotes/note-page.py <src> --markup       # raw <p> HTML, for punctuation and links
python3 scripts/relnotes/note-page.py <src> --audit        # code-formatting inconsistencies only
python3 scripts/relnotes/note-page.py <src> --check-links  # resolve every link the authors wrote
python3 scripts/relnotes/note-page.py --check-url <url>…   # resolve links you mean to suggest
```

It walks sections and notes in document order and reports each note's id, bug link and inline markup.
Two reasons not to rebuild it inline: **inline Python prompts** — measured, see `command-forms.md`, so
an ad-hoc snippet stops the review dead — and the snippets were wrong. Every hand-written version
matched `id="note-\d+"`, which silently skips the `note-mdn` item the Developer section carries, so
each pass quietly reviewed one note fewer than the page contains.

The `--audit` pass flags calls like `getBBox()` and dotted names like `security.webauth.u2f` sitting in
prose. It is deliberately narrow and says so in its own output: it does **not** see single-word API
names such as `StylePropertyMap`, hyphenated keywords, or lowercase words like `protocol`. Rules broad
enough to catch those flagged one shipped note in five, on `macOS` and `JavaScript` and
`drag-and-drop`. A clean audit therefore means nothing *of those two shapes* — the abbreviation and
code-formatting rules in `style-guide.md` still have to be read.

**When an edit doesn't show up, ask Nucleus — don't re-fetch the page.** The rendered page is built
from Nucleus on a delay, so straight after the author saves, "the change isn't there" has two causes
that look identical from the page: it was never saved, or it hasn't published yet. Re-fetching cannot
tell them apart, and a cache-busting query string appended to someone else's URL is not an answer:
a pass fetched the page three times that way, twice with `?freshness=…`, before reasoning its way to
publish lag. One call settles it, because Nucleus is where the edit lands first:

```
python3 scripts/relnotes/fetch-shipped-notes.py --channel Nightly --notes-for 155.0a1
```

Present in Nucleus but not on the page means published-pending. Absent from both means it was not
saved. Say which one it is rather than asking the author to check again.

**Check links with `--check-links`, and URLs you are about to suggest with `--check-url`; never
`curl` per URL.** Notes link to 152 different hosts
across the corpus — MDN, spec drafts, RFCs, Google Play, Connect — and only nine are on the `curl`
allowlist, so a link sweep by `curl` prompts for approval on most of its URLs. `--check-links` fetches
through Python, which needs no per-host entry, and it attributes each result to the note that carries
the link. It separates a genuinely moved page from an equivalent redirect: MDN sends every locale-less
URL to `/en-US/…`, which is how notes are supposed to link to it, so those are counted and not listed.

## Getting the draft

Direct Google Drive access is **not** available here (no Drive/Docs integration), so:

1. **Pasted text or an exported file** — most reliable. Ask for a paste, or read an export
   (`.txt`, `.md`, `.docx`, `.html`) with the Read tool.
2. **A "Published to the web" Google Doc** — if the author did File → Share → Publish to web, the
   public `docs.google.com/.../pub?...` URL works with WebFetch. A normal share link (`/edit`,
   `/view`) is authenticated and will fail — ask them to publish to web or export.
3. If you only have a private share link, say it can't be opened and ask for a paste, export, or
   published-to-web URL.

**Dot releases come as a staging URL, not a Google Doc.** For a dot release (e.g. 151.0.4) the notes
are generally a rendered staging page — e.g.
`https://www-dev.springfield.moz.works/en-US/firefox/151.0.4/releasenotes/`. Read it with WebFetch
like any public page. Two consequences:

- It's the *rendered* page, so it already reflects final layout and tags — review it as published
  output.
- Dot releases **require bug links** (the opposite of mainline), so expect and check linked bugs
  rather than flagging them.

**Publish lag when re-checking edits.** The `/pub` snapshot regenerates on a delay (the doc header
usually says "Updated automatically every 5 minutes"), and WebFetch caches each URL for ~15 minutes,
so re-fetching the *same* URL can return your own earlier pre-edit copy. When you re-check whether
changes were applied and the old text is still showing:

- **Bust the WebFetch cache** with a throwaway query param (`…/pub?freshness=recheck2`) so it's
  treated as a new URL.
- If the change still isn't there after a genuinely fresh fetch, it's most likely publish lag, not a
  missed edit. **Don't tell the author they forgot.** Say you're seeing a stale published snapshot,
  wait a few minutes, and re-check before concluding anything.

Before reviewing, confirm the **target**: which product (Firefox Desktop, Firefox for Android/iOS,
Focus), which channel/version, and whether these are mainline, dot-release, beta, ESR/Enterprise, or
known-issues notes. Audience scoping depends on it.

## Mapping notes to bugs

Drafts usually don't include bug numbers, but the author may ask whether two notes should be
consolidated, recategorized, or rescoped. Answering well often needs to know what actually changed —
see `bugzilla-access.md` for the REST-vs-MCP split, the query-link translation, and the **platform
scoping rule** (a bug's component may name one OS while the fix is platform-agnostic; the changed
file paths are definitive).

**Don't ask the author for a bug list — generate it.** `cf_tracking_firefox_relnote` is queryable
over REST, so the flagged set for a release is one command:

```
python3 scripts/relnotes/relnote-flag.py --approved 155        # bugs flagged 155+
python3 scripts/relnotes/relnote-flag.py --coverage 155.0a1    # note set vs flagged bugs
python3 scripts/relnotes/relnote-flag.py --coverage 153.0.3 --product "Firefox for Android"
```

`--coverage` reports both directions against the published note set: flagged bugs with no note, and
notes whose bug was never flagged. Validated against Nightly 155 — 19 notes, 19 flagged bugs, exact
correspondence both ways.

**Read its output with the four known distortions in mind**, all of which it labels rather than
hides:

- **The flag is per-major**, so a bug flagged `153+` may be noted in 153.0 *or* any later dot
  release. Coverage therefore spans every release of the major by default; `--scope release` narrows
  it when you want to know what one release shipped.
- **Nucleus stores one bug per note**, but a note can cite several. The check also reads bug links
  out of the note text, and says when a match came only from there.
- **A meta bug or a rollup note may carry the note** while the flag sits on an implementation bug.
  One hop over `blocks`/`depends_on` explains those, reported separately from real gaps.
- **`Fixed` and known-issue notes are normally unflagged** — Release Management writes dot-release
  fixes without setting the flag, so they are counted, not listed as findings.
- **The flag has no product dimension.** Pass `--product` for a mobile note set, or you will be shown
  the desktop one for the same version number. Even then, "flagged but no note" is only reported for
  desktop: a bug flagged `153+` is destined for whichever product Release Management chose, so
  checking Android against it would report every desktop-flagged bug as a note owed on Android.

**Reviewing a release that hasn't shipped needs no special flag.** Notes on an unpublished Nucleus
release are counted by default, because reviewing before a release goes live is the normal case and
those notes are exactly what is under review. The header names any draft release it counted, so you
can see what the number rests on. `--published-only` answers the narrower question of what has
actually shipped — reach for it when auditing a past cycle, not when reviewing an upcoming one.

Still ask the author for anything the flag cannot tell you: which draft is current, and the
developer's suggested wording. And treat a coverage report as a prompt to look, never a verdict —
security-restricted bugs are omitted by REST with no count, so it can never prove a flagged bug is
absent.

If the `moz` MCP isn't connected, `@moz:` resources won't resolve — say so and ask the author for
the bug summary rather than guessing.

## Review process

1. Acquire the draft and confirm product/channel/version.
2. Check each note against every applicable rule in `style-guide.md`. Pay special attention to:
   - **Scoping** — is a developer-only change in a user-facing tag, or vice versa? Is anything too
     internal to belong in user notes at all?
   - **Verb/tense and full stops** — remembering that present-tense New/Changed notes are correct
     and only **Fixed** needs the past-tense verb lead.
   - **Jargon, abbreviations, and `about:config` mentions.**
   - **Localized URLs and stray bug links** (allowed only for dot releases).
   - **Correct tag** for each entry.
3. Watch for issues the rules don't enumerate: duplicated or overlapping notes, inconsistent
   capitalization of feature names, vague impact ("improved performance" with no specifics), and
   notes that bury the user benefit.
4. Apply the two wording traps from `style-guide.md` — conflated facts and buried benefit. Looking
   up the bug is usually what reveals a note is compressing two distinct facts.

**Don't guess.** Flag anything uncertain as a question rather than a hard correction, especially
audience-scoping calls that depend on product context you don't have. If you couldn't read a bug or
patch, say the scoping question is open rather than asserting a scope.

## Output

- A short summary (overall quality, biggest themes).
- A per-note list of issues, walking the notes **in the same order they appear in the document, top
  to bottom** (by section, then by note). The author reviews with the doc open and works straight
  down it, so matching that order lets them apply comments in place. Don't reorder by severity or
  theme, and don't group all the "jargon" or all the "consolidation" items together.
- For each note: the original text, what's wrong (cite the rule), and a concrete suggested rewrite.
- **Always show the full final note with all changes applied — not just a description of the
  changes.** Even for a small tweak (a comma, an added bug link, one reworded clause), write out the
  complete note as it should read so the author can copy it straight in. Describing the change alone
  forces them to reconstruct the result.
- For a consolidation, raise it at the position of the **first** note involved and name the others
  (and their positions) it merges with, so it stays in reading order.
- A short cross-cutting section *after* the in-order walk for anything genuinely spanning the whole
  document (a coverage check against the bug query, missing full stops throughout, terminology
  consistency). Keep per-note issues in the walk.
- Questions attached to their note in the walk; optionally restated in a short list at the end.

Keep suggestions concrete and copy-pasteable so the author can apply them directly.
