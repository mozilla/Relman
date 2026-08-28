# Release Notes Style Guide (working summary)

Shared by `find-release-note-candidates` (drafting) and `review-release-notes` (critique), so
the rules live in one place and the two skills cannot drift apart.

**The wiki wins on any conflict:**
<https://wiki.mozilla.org/Release_Management/Release_Notes#Release_Notes_Style_Guide>

For *what clears the bar* rather than *how to word it*, see
[`shipped-notes-survey.md`](shipped-notes-survey.md) — the empirical calibration built from every
published note of the last two years.

## Audience and focus

- Write for a broad, international, non-technical audience. Avoid jargon and colloquialisms that
  don't translate.
- For new or changed features, focus on **how it affects the user's experience**, not what the
  software does internally.
- Avoid abbreviations — spell terms out. "preference", not "pref"; "Developer Tools", not
  "DevTools". This applies in the Developer/Web Platform sections too.
- Never mention `about:config` preference names in note text — **for anything riding the trains.**
  **Nightly-only notes are the deliberate exception**: their audience is testers who need to know
  which preference to set, and naming it is established practice. Precedent in the corpus:
  *"the Temporal proposal … available for experimentation in Nightly builds behind the
  `javascript.options…`"* (135 Nightly) and *"disabled by default using the preference
  `layout.css.module-scripts.enabled`"* (145 Nightly). Naming a preference in a note that will ship
  to release is the real problem; naming one in a Nightly-only note is fine.
  **And the stronger form: if the only way to get the benefit is to change a preference manually in
  `about:config`, it is not a release note at all.** Notes describe what users get by default. A change that merely makes something
  configurable is not noteworthy on its own — bug 1418178 (making the number of Ctrl-Tab previews
  configurable) was skipped on exactly this basis.
- **Developer / Web Platform notes are the exception to the plain-language rule**: that audience is
  developers, so these may be technical, reference APIs, and use inline `code` and MDN links.
## Wording and grammar

- **Fixed** notes lead with a **past-tense verb**: "Fixed", "Removed", "Improved", "Updated".
  Measured: 283 of 337 substantive `Fixed` notes from the last two years — **84%** — do this (the
  count excludes the formulaic security and stability boilerplate). **One exception outranks it:** a
  note scoped to one product leads by naming that product — see the mobile section.
- **New** and **Changed** notes are **present-tense descriptive** — "X now does Y". Do not flag a
  present-tense New/Changed note for lacking a past-tense verb; that is the expected register. Only
  **Fixed** needs the verb lead.
- End **every** note with a full stop, including short ones.
- **Don't address the reader as "you".** Release Management's review guidelines call for avoiding
  second person; write impersonally or in the third person. The wiki's own worked example does this
  — its improved version reads *"…they'll now be prompted to finish installation"*, not "you'll be
  prompted".
  - *"Autofill prompts now dismiss when focus moves away"*, not *"…when you move focus away"*.
  - **Note the corpus disagrees with the guideline:** roughly 9% of shipped notes contain "you"
    (measured 2026-08-15), many of them recent. Don't take phrasing from published notes as licence
    here — follow the guideline, not the sample.
- Keep it short. The median shipped note is ~20 words. A candidate needing three sentences is
  usually either two notes or not a note.
- Defer to MDN writing conventions for capitalization, contractions, numbers, pluralization,
  apostrophes and quotation marks, commas, hyphens, and spelling (American).

## Links

- De-localize all URLs — strip the `en-US/` (or other locale) segment.
- Don't link bugs in finalized **mainline** notes. **Dot releases require bug links** — the
  opposite rule. (Visible in the corpus: 42% of major notes carry a bug number versus 88% of dot
  notes.)
- When a bug link's anchor text uses the word "Bug", capitalize it — "Bug 2047850". Link the whole
  `Bug NNNN` string, with the parentheses left outside the anchor.
- **The full stop goes before the parenthetical bug link, and nothing follows the closing paren:**
  `Fixed audio failing to play on some sites. (Bug 2056444)` — not `… on some sites (Bug 2056444).`
  The note is a complete sentence; the bug reference is an annotation appended after it. Of 40
  published dot-release notes carrying a bug link from 149.0.2 through 153.0.2 (desktop and
  Android), 36 use this form. The only exceptions are the four Android 153.0.1 and 153.0.2 notes,
  which drifted to the trailing-period form — recent, and not the convention to copy.

## Categorization (tags)

- **New** — new features.
- **Fixed** — resolved bugs. Historically a mostly dot-release tag; see the survey for where the
  major-release threshold currently sits and how fast it is moving.
- **Changed** — interface or behavior modifications.
- **Developer** — the developer *tools*: Debugger, Inspector, Netmonitor, the DevTools panels.
- **HTML5** — the tag that renders as the **Web Platform** heading on the page. There is no
  `Web Platform` tag in Nucleus; this is it. Use it for what ships *in the engine* for web content
  to use: CSS properties, HTML elements, HTTP headers, JS APIs.
- **Choosing between those two is not "technical vs not"** — both audiences are developers. It is
  **who ships the thing**. A TC39 proposal adding JS methods is `HTML5`, not `Developer`: precedent
  at 115.0 (`Array.prototype` copy methods), 122.0 (`ArrayBuffer.prototype.transfer`), 139.0
  (Temporal), 144.0 (`Map.getOrInsert`), 148.0 (`Iterator.zip()`). **Raise it when a note is on the
  wrong side of that line**, including a move between the two sections — the rule below is about not
  churning an established convention, not about leaving a misplaced note alone.
- Don't normalize `HTML5` and `Developer` into each other across a product's existing usage; where
  both are defensible, match what the target product already uses.
- **Firefox Labs** — experimental, opt-in features that ship preference-off and are enabled from
  the Firefox Labs panel in Settings. This is the correct home for such features — don't move them
  to New or Web Platform, and don't treat the section as a stray tag. The note must say the feature
  is experimental and how to enable it, and must **front-load the experimental/off-by-default
  nature in the first sentence**. Don't open with "Firefox now supports X" and clarify opt-in
  later; a reader who skims the first line is left believing it is on by default. Lead with the
  qualifier: "Firefox now offers experimental support for X… It can be enabled from the Firefox
  Labs panel in Settings."
- **Community** — contributor credits. Generated elsewhere (see below). Individual contributors are
  *occasionally* named inline in another tag's note (e.g. an experimental CSS property credited to
  its implementer). It is unusual enough to be worth querying, but it is not an error.
- **Enterprise** — one link out to the enterprise release notes, maintained separately at
  [firefox-admin-docs.mozilla.org/release-notes](https://firefox-admin-docs.mozilla.org/release-notes/).
  **Not ours to write, and not hunted for during the cycle.** Doing that was tried in August 2026 and
  reverted on 2026-08-27: the content is referenced from too many places to live anywhere but one
  canonical page. The only thing to check here is the link — see *Sections to skip*.

## Nightly release notes

Nightly has its own notes (~100 Nightly releases and ~1,000 notes), used to surface
Nightly-only changes for testing and feedback. The matching Bugzilla flag is
`cf_tracking_firefox_relnote` = **`nightly+`**.

**The convention: name the first version that supported it, and make Nightly-only unmistakable.**

> Starting with Firefox 154, Firefox Nightly now supports QUIC version 2 (RFC 9369) for HTTP/3
> connections.

> Starting with Firefox 153, Nightly builds support the new JPEG XL image format, which generally
> has better compression than WebP, JPEG, PNG and GIF.

> Starting with Firefox 154, Firefox Nightly now supports the `alpha()` CSS function.

Two things the data shows that are easy to get wrong:

- **The version cited is usually *not* the Nightly the note appears in.** Of the notes using this
  opening, 58% cite an *earlier* version (lag of 1–4 releases). A note added for Firefox 153 keeps
  saying "Starting with Firefox 153" as it carries forward into the 154 and 155 Nightly notes.
  **Cite the version the feature first shipped in, not the current one.**
- **Say Nightly explicitly in the sentence**, not just by living in the Nightly notes. Common
  phrasings: "Nightly builds now…", "Firefox Nightly now supports…", "Firefox Nightly ships…".

About 37% of Nightly notes use the "Starting with Firefox N" opening; the rest are plain descriptive
notes ("The JSON Viewer now shows a breadcrumb path…"). Use the prefixed form when the point is
*which version began supporting this* — which is the usual reason a Nightly note exists.

Tag distribution differs sharply from release notes: Nightly skews to **New** (452) and **HTML5**
(366), i.e. web-platform features arriving behind a preference. `Fixed` is rare (48).

A feature can earn a Nightly note *and*, later, a release note — JPEG XL had a Nightly note from 153
and a **Firefox Labs** note when it reached release.

**Retire a Nightly note once the feature rides the trains.** The Nightly note exists to tell testers
about something they can only get in Nightly; when the feature ships to release, that purpose is
spent and the release note replaces it. **This holds even when the note carries a "keep until version
N" directive** — those are written while the feature is still Nightly-only and go stale the moment it
is enabled on release. Worked example: the `attr()` Nightly note ran 152 → 154 with an inline
`<!-- Keep this note until 156 -->` comment, and was correctly dropped from 155 when bug 2038940
enabled the feature on release, with a plain (non-Nightly-framed) note taking its place.

**The other retirement trigger is age: three Nightly cycles.** `nightly+` runs a note for three
Nightly releases or until the feature is enabled by default, whichever comes first. A note that is
*still* Nightly-only after three is **expired** — accurate wording, due to be dropped from the next
set — which is a different state from **stale**, where the feature has shipped and the note still
says "Firefox Nightly". Expired wants deleting; stale wants rewriting now. Resolve the gate before
deciding which, per [`gating.md`](gating.md).

**Count the sets the note is attached to, not the version in its text.** "Starting with Firefox N"
names when the feature landed, not when the note started running, so counting cycles off the prose
overcounts. The two carry-forwards in the 155 Nightly set look identical on the page and are not:
2046153 (`link-parameters`) has run 153, 154 and 155, so it drops from 156, while 2045278
(`ellipse-corners`) also opens "Starting with Firefox 153" but is attached to 154 and 155 only and
has a cycle left. A review treated both as three-cycle expiries on the strength of the wording.
`fetch-shipped-notes.py --search '<text>'` lists the releases each note is attached to, and says
when that list is clipped.

## Known issues

Focus on user impact. If a workaround exists, give clear step-by-step instructions.

## Sections to skip when reviewing

- **Enterprise** — one link out to the separately maintained enterprise release notes. No wording to
  review, but **check the link before go-live**, and note that a plain link check is not enough:
  - **It must be version-specific** — `…/release-notes/version/firefox-<N>/`, not the bare
    `…/release-notes/` index. The index serves every version on one growing page led by the newest,
    so a bare link read from an archived note leaves the reader hunting. Pinned links keep working:
    pages stay published, currently back to Firefox 143.
  - **The version in the URL must match the release under review.** This is the check that catches
    something. A link left over from the previous release resolves perfectly well, because that
    version's page is still live — so liveness alone would report it clean.
  - **Then confirm it resolves.** An unpublished version 404s, which is what makes it checkable.
- **Community contributions** — generated elsewhere, not hand-authored. Disregard entirely.
- **Any empty section** — the draft is an intermediate staging document and empty sections don't
  carry over to the publishing system. Ignore them; don't flag them as needing to be cleared.

## Mobile (Android and iOS)

**There are no separate Android note sets until a release ships.** For **Nightly and Beta**, Android
notes live in the same set as desktop — an Android-only note in `155.0a1` is correct placement, not a
misfile. Because the set is shared, **say in the note that it applies to Firefox for Android**; the
established form links the Play Store listing: *"On [Firefox for Android][1], autofill prompts now
dismiss when focus moves away…"*. This is long-standing practice in the corpus (Android-scoped notes
appear in the 122, 125, 126, 128, 132, 133 and 135 Nightly sets). Separate Android release notes
begin only at release, where they appear under their own product (`Android 131.0/Rel`).

**A note that applies to both Desktop and Android is written once and associated with both
releases.** This is a wiki rule, and it is how Nucleus stores it — one note object carrying a list of
releases, not a copy per product. The 153.0.3 Blob URL media fix is a single note (`791964`,
bug 2056444) attached to both `Firefox 153.0.3` and `Firefox for Android 153.0.3`, which is why it
renders identically on both pages.

Two consequences when reviewing:

- **Editing the wording changes every release it is attached to.** A rewrite requested "on the
  desktop page" lands on the Android page too.
- **"Remove this note from the desktop page" means dissociating it from that release**, not deleting
  the text. Say which you mean, because deleting the note removes it from the other product as well.
  Ask whether the note should be dissociated or reworded to cover both.

**The "On Firefox for Android" lead takes precedence over the `Fixed` past-tense verb lead.** In a
shared note set the reader has to know the scope before anything else, so scope goes first even
though it costs the past-tense opening. *"On Firefox for Android, autofill prompts now dismiss when
focus moves away"* is correct as a `Fixed` note; don't "correct" it to *"Fixed autofill prompts not
dismissing … on Firefox for Android"*.

Generic, reusable notes are used unless Product supplies their own:

- **Generic notes** — no review needed. Skip, **and skip every restatement of them further down the
  document.** An iOS section ends with a `For Bitrise:` block, which is the same generic text
  flattened for a store field that rejects newlines: its literal `\n\n` is deliberate, and a
  difference between two copies of the generic text is not a finding.
- **Product-supplied notes** — **light review only**. Product provides these in the form they
  want; flag only clear errors (typos, broken or localized links, obviously wrong scoping) and
  otherwise leave the wording alone. Don't apply the full desktop critique.
- **Mobile dot releases are the exception** — authored by Release Management, so give them the
  full desktop-style critique, with bug links required.

If it's unclear whether mobile notes are generic or Product-supplied, ask. **Don't infer it from
past releases.** Both happen on majors, and the mix is moving: Release Management increasingly
authors mobile notes off its own discovery passes, so a run of Product-supplied majors is not
evidence about the next one. Asking costs a sentence; assuming costs a mobile note the full review
it needed.

## Tone reference

Real shipped notes, quoted verbatim, for **register only**. Several use second person, which the
guideline above tells you not to copy — they are here to show tone and length, not phrasing:

- New: "Private Browsing Mode now allows you to instantly clear all data from your current session
  without closing the entire window."
- New: "Local Firefox profile backups are now available on Linux in addition to Windows, and you
  can restore them across platforms."
- Fixed: "Fixed incorrect screen resolution reporting to websites in multi-monitor setups."
- Fixed: "Various security fixes." (the standing per-release catch-all)
- Changed: "Geolocation on Windows now respects the user's Windows location permission setting,
  instead of overriding it, when the user grants location permission to a page."

Note the contrast: user-facing notes stay plain-language and impact-focused; Web Platform and
Developer notes may be technical and link to MDN. The survey's "Representative notes" section has
many more, grouped by tag and sorted by length.

## Two recurring wording traps

1. **Conflated facts.** When a note reads awkwardly, check whether it compresses two distinct
   facts into one phrase — often by stacking two modifiers on one noun where each describes a
   different thing. "more and smaller increments" was really *more zoom levels* **and** *smaller
   increments between them*. Tease them apart so each fact gets its own noun; the cleaner wording
   then falls out ("now offers more zoom levels in smaller increments than before"). Looking up the
   bug to see what actually changed is usually what reveals there were two facts.
2. **Buried benefit.** Vague impact ("improved performance") with no specifics, or the user benefit
   arriving after a clause of mechanism. Lead with what the user gets.

**A `Changed` note may lead with the change itself, and that is not the buried-benefit trap.** The
trap is about vague impact and about mechanism arriving before meaning; it is not a ban on naming the
change. When the subject of the note *is* a behavior Firefox now enforces differently, the clearer
order is the change first, then how people encounter it — Release Management's framing, deciding this
on bug 299116: *"Firefox changed something compared to what it used to do, and here's how that'll
affect you."* The effect still has to be in the note. A `Changed` note that names the change and stops
is squarely in the trap.
