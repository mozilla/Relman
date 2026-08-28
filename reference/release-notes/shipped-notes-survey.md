# What actually gets a release note

Empirical calibration for the release-note skills: every **Firefox / Release** note published since **2024-08-01**, pulled from Nucleus and joined to its release.

**That floor is a rolling 24-month window** (`--months`), not a fixed date: a refresh drops the oldest releases as well as adding new ones. So the absolute counts here move in both directions, and a corpus that *shrank* between two revisions of this file is the window sliding rather than data going missing. The medians and rates are the parts meant to be quoted.

Regenerate with `scripts/relnotes/fetch-shipped-notes.py --format md -o reference/release-notes/shipped-notes-survey.md --areas --negative 153.0,152.0,151.0,150.0`. Counts below are from the run recorded at the bottom of this file; refresh after each cycle.

## The bar, in one line

**A major release ships 20 notes (median; range 10–32) drawn from ~2,648 bugs fixed per cycle — about 1.0%, or 1 note per 98 fixed bugs.**

**This is a publication rate, not a shortlist budget — do not use it to prune.** It is what survived *after* a developer was asked and Release Management made a call. Discovery sits upstream of both: its job is to surface bugs worth asking a developer about, and the cost of the two errors is wildly asymmetric. A surplus candidate costs one question in a bug; a missed one ships a release with a note nobody wrote. So **err toward including** and let the tiering carry the uncertainty.

What the number *is* good for: sanity-checking the final published set, and calibrating the *shape* of a note-worthy change (below) so the asking is well-targeted rather than indiscriminate.

## Notes per major release

| Version | Notes | | Version | Notes |
|---|---:|---|---|---:|
| 129.0 | 20 | | 142.0 | 18 |
| 130.0 | 10 | | 143.0 | 17 |
| 131.0 | 15 | | 144.0 | 27 |
| 132.0 | 20 | | 145.0 | 24 |
| 133.0 | 12 | | 146.0 | 21 |
| 134.0 | 10 | | 147.0 | 30 |
| 135.0 | 21 | | 148.0 | 19 |
| 136.0 | 29 | | 149.0 | 26 |
| 137.0 | 14 | | 150.0 | 26 |
| 138.0 | 18 | | 151.0 | 29 |
| 139.0 | 22 | | 152.0 | 23 |
| 140.0 | 19 | | 153.0 | 30 |
| 141.0 | 16 | | 154.0 | 32 |

26 major releases, 548 notes, mean 21.1. Plus 58 dot releases carrying 242 notes.

## Tags, and what each is really used for

| Tag | Notes | In majors | In dots | Has bug | Median words |
|---|---:|---:|---:|---:|---:|
| Fixed | 276 | 56 | 220 | 222 | 16 |
| New | 189 | 184 | 6 | 83 | 30 |
| HTML5 | 148 | 147 | 1 | 82 | 21 |
| Developer | 62 | 62 | 0 | 29 | 23 |
| Changed | 51 | 49 | 2 | 29 | 28 |
| Community | 26 | 26 | 0 | 0 | 134 |
| (untagged) | 21 | 18 | 9 | 15 | 43 |
| Enterprise | 4 | 0 | 4 | 4 | 17 |
| Labs | 4 | 4 | 0 | 3 | 45 |

Notes are short — median **20 words** across hand-authored notes (excluding the generated Community credits). A candidate that needs three sentences to explain is usually either two notes or not a note.

**The Fixed/major split is the number to watch.** Of 276 `Fixed` notes, 220 shipped in dot releases against 56 in majors — and 26 of those majors are just the standing security catch-all. Majors are carried by `New`, `HTML5`/`Developer`, and `Changed`; dot releases are where fixes live. Treat that as a description of past practice rather than a rule — it is already changing, and the next section quantifies how fast and characterizes the threshold.

### Opening words by tag

The style guide's tense rule shows up in the data — use this to check a draft's register against what actually ships.

- **Fixed** — `Fixed` (194), `Various` (38), `Improved` (10), `Security` (8), `The` (3), `Added` (2), `On` (2), `Firefox` (2)
- **New** — `Firefox` (37), `The` (19), `You` (12), `Address` (8), `On` (8), `Added` (6), `Users` (5), `A` (4)
- **HTML5** — `Firefox` (44), `Added` (33), `The` (29), `Support` (7), `Enabled` (4), `Implemented` (3), `Service` (2), `Both` (1)
- **Developer** — `The` (17), `Firefox` (11), `Added` (5), `A` (4), `Support` (2), `Improved` (2), `When` (2), `Debugger` (1)
- **Changed** — `The` (13), `Firefox` (7), `When` (3), `Improved` (3), `On` (3), `Due` (2), `Extensions` (2), `Local` (2)
- **Community** — `With` (26)
- **(untagged)** — `strike` (7), `For` (3), `Windows` (2), `On` (2), `Following` (2), `Users` (1), `Due` (1), `Multiselecting` (1)

### Representative notes

Real shipped text, spread from shortest to longest within each tag.

**Fixed**

- Security fix.
- Fixed Firefox adding a new Start Menu shortcut on Windows every time it started.
- Improved overall stability by fixing crashes related to browsing, graphics, and accessibility features. (Bugs 2001160, 1998185, 1998188)
- Improved Firefox' connection fallback behavior to mitigate sporadic slow/failed loads on some Google sites on systems with certain third-party software installed.
- When the timepicker is enabled for `<input type="time">` and `<input type="datetime-local">`, it now provides full keyboard and assistive technology support. This update also improves the behavior of the time spin buttons for users who prefer reduced motion. The Firefox Accessibility team hopes t...

**New**

- Support HEVC playback on Linux.
- You can now remove extensions from the sidebar by right-clicking the extension icon and selecting `Remove from Sidebar`.
- You can now close a Picture-in-Picture window without pausing the video. Press `Shift + Click` on the close button or use `Shift + Esc` to exit while keeping playback uninterrupted.
- We’re excited to share another tab groups update that addresses a top request from our community! You can now drag a tab into a collapsed group without automatically expanding it. It’s a quick way to stay organized while minimizing visual distractions. <video src="https://www.mozilla.org/media/im...
- ***Firefox Address Bar Refresh 2025 - new ways to search for things new, previously viewed, and more - all from the address bar:*** **Features** *Unified Search Button*: A new, easy-to-access button in the address bar helps you switch between search engines and search modes with ease. This featur...

**HTML5**

- Enabled the spec-compliant `HTMLMediaElement.captureStream()` API.
- The `getCapabilities` method allows applications to gather the media capabilities supported for the live MediaStreamTrack.
- Added support for closing popovers & dialogs with the Android *Back* Button, and implemented the `CloseWatcher` API for handling this in script.
- Firefox now supports the `text-box-trim` and `text-box-edge` CSS properties, and the associated `text-box` shorthand. These properties control the leading space above and below text, enabling simpler and more consistent vertical spacing and alignment.
- The `fetchpriority` attribute enables web developers to optimize resource loading by specifying the relative priority of resources to be fetched by the browser. It accepts three values: `auto` (default priority), `low` (lower priority), `high` (higher priority). It can be specified on `script`, `...

**Developer**

- Firefox now supports the Permissions API in `Worker` Context.
- The toolbar of the Storage Inspector now has a button to delete all entries of the currently selected storage.
- Improved support for debugging web extensions, such as automatically reloading the web extension's source code in the Debugger when the extension is reloaded.
- A new column has been added to the Network panel to display the full path of the request URL. This enhancement makes helps developers quickly view and analyze complete request paths.
- The Debugger's directory root is now scoped to the specific domain where it was set, which aligns with typical usage and avoids applying it across unrelated domains. This builds on previous improvements such as a redesigned UI and easier removal of the root setting. Setting a directory root updat...

**Changed**

- Rededicated previous color settings to Contrast Control settings. (Learn more)
- Firefox on Windows 11 now uses system provided font icons for the caption buttons, more in line with Windows 11 conventions.
- When a PDF or other file that Firefox opens directly finishes downloading, it now opens in a background tab if you've switched tabs or closed the original page.
- The *TrustPanel* combines the privacy and security panels accessed from the address bar to give the user one place to check the *Privacy and Security* settings of the current page. Learn more. <img alt="TrusPanel Screenshot" src="https://www.firefox.com/media/img/firefox/releasenotes/note-images/...
- The refreshed New Tab layout previously rolled out in Firefox 134 to users in the United States is now being made available in all countries where Stories are available. It features a repositioned logo to prioritize Web Search, Shortcuts, and Recommended Stories at the top. The update also includ...

**Community**

- With the release of Firefox 132, we are pleased to welcome the developers who contributed their first code change to Firefox in this release, 7 of whom were brand new volunteers! Please join us in thanking each of these diligent and enthusiastic individuals, and take a look at their contributions...
- With the release of Firefox 141, we are pleased to welcome the developers who contributed their first code change to Firefox in this release, 12 of whom were brand new volunteers! Please join us in thanking each of these diligent and enthusiastic individuals, and take a look at their contribution...
- With the release of Firefox 139, we are pleased to welcome the developers who contributed their first code change to Firefox in this release, 14 of whom were brand new volunteers! Please join us in thanking each of these diligent and enthusiastic individuals, and take a look at their contribution...
- With the release of Firefox 138, we are pleased to welcome the developers who contributed their first code change to Firefox in this release, 18 of whom were brand new volunteers! Please join us in thanking each of these diligent and enthusiastic individuals, and take a look at their contribution...
- With the release of Firefox 151, we are pleased to welcome the developers who contributed their first code change to Firefox in this release, 37 of whom were brand new volunteers. Please join us in thanking each of these diligent and enthusiastic individuals, and take a look at their contribution...

**(untagged)**

- Windows users with Avast or AVG security software may experience a crash when visiting certain sites.
- <strike> Some websites on internal or corporate networks that require a login prompt may fail to display the sign-in dialog in Firefox 150, showing a blank page instead.</strike> (Fixed in 150.0.2 in Bug 2034752)
- On Linux (Wayland) systems, certain popups and context menus will sometimes open in the wrong place, such as the top-left corner of the window. Firefox 147.0.3 has a partial mitigation for this problem and remaining instances will be addressed in a future release.
- <strike>Under certain conditions, copyrighted video served via digital rights management may experience playback issues . A patch is underway. For an immediate workaround, please complete the following steps: 1. Go to *about:config* in the Awesomebar. 2. Search for `media.eme.mfcdm.origin-filter....
- When toolbars are hidden in fullscreen mode (the default on Windows and Linux), the vertical tabs sidebar does not appear when moving the cursor to the left edge of the screen. As a workaround, move the cursor to the top of the screen to reveal both toolbars and sidebar together. Alternatively, y...

**Enterprise** — only 4 in the corpus, so read these as instances rather than a pattern

- Added an enterprise policy to disable the *Firefox Labs* section in *Settings*.
- Introduced a new enterprise policy that allows administrators to directly manage and control AI-related features within Firefox.
- Fixed an issue where enterprise policies for the browser homepage and start page were not being applied correctly.
- Introduced a new enterprise policy that allows administrators to prevent the built-in VPN and IP protection features from being available to users. (Bug 2022365)

**Labs** — only 4 in the corpus, so read these as instances rather than a pattern

- Firefox Labs can now be opened quickly by typing "labs" or "experiment" in the address bar and selecting the _Open Firefox Labs_ quick action. <img src="https://www.firefox.com/media/img/firefox/releasenotes/note-images/153_labs_quickaction.png" alt="Screenshot of the Firefox Labs quick action be...
- Firefox now offers experimental support for the new JPEG XL image format, which generally provides better compression than WebP, JPEG, PNG, and GIF and is designed to supersede them. You can enable it from the *Firefox Labs* panel in *Settings*.
- Firefox now offers experimental support for the new JPEG XL image format, which generally provides better compression than WebP, JPEG, PNG, and GIF and is designed to supersede them. You can enable it from the *Firefox Labs* panel in *Settings*. <img src="https://www.firefox.com/media/img/firefox...
- Tab notes feature that lets you attach a short note to a web page is now available in Firefox Labs. You can use notes to remember why you opened a page, what you planned to do next or any details you want to revisit later. Please give notes a try and share your feedback on what works well and wha...

## Where the `Fixed` threshold currently sits (majors)

56 `Fixed` notes shipped in a major release in this window — but **26 of those are the standing `Various security fixes.` catch-all**, one per release. The real count of substantive mainline fix notes is **30** across 26 releases.

**And that bar is already moving:**

| Window | Substantive `Fixed` notes per major |
|---|---:|
| 129.0–141.0 | 0.46 |
| 142.0–154.0 | 1.85 |
| 148.0–154.0 (most recent) | 2.29 |

Per release: 129.0 (0), 130.0 (1), 131.0 (0), 132.0 (0), 133.0 (1), 134.0 (0), 135.0 (1), 136.0 (1), 137.0 (0), 138.0 (0), 139.0 (2), 140.0 (0), 141.0 (0), 142.0 (2), 143.0 (0), 144.0 (1), 145.0 (0), 146.0 (1), 147.0 (4), 148.0 (2), 149.0 (1), 150.0 (1), 151.0 (3), 152.0 (4), 153.0 (0), 154.0 (5). 11 of 26 majors carried no substantive fix note at all — and nearly all of those are in the older half of the window.

So the practice has already shifted by roughly 5× without anyone changing the guidance. Any deliberate decision to lean on `Fixed` for majors as the cycle shortens is an acceleration of a trend in progress, not a new departure — which makes the threshold question the practical one. The full list below is the evidence base.

- **130.0** — Fixed an issue where `Copy` and `Paste` context menu items intermittently were not enabled when expected. (bug 1863246)
- **133.0** — The “Picture-in-Picture: auto-open on tab switch” feature from Firefox Labs now behaves more reliably across a wider range of sites, automatically opening relevant videos while ignoring others.
- **135.0** — Made improvements to the Translations feature which will reduce the likelihood that models will invent new, made-up words under some circumstances.
- **136.0** — Firefox will now prefer the PNG format when copying images out of Firefox, allowing the preservation of transparency.
- **139.0** — PNG images with transparency now keep their transparency when pasted into Firefox.
- **139.0** — The upload performance of HTTP/3 has been significantly improved, particularly on resumed connections (QUIC 0-RTT) and high-bandwidth and high-delay connections.
- **142.0** — Improved the scroll speed in the bookmarks dialog to not go beyond the component area. (bug 1957280)
- **142.0** — Improved drag-and-drop support for blob images. (bug 1670200)
- **144.0** — The following languages have improved translation quality: - Arabic - Bulgarian - Catalan - Chinese (Simplified) - Czech - Dutch - Estonian - Finnish - French - German - Hungarian - Italian - Japanese - Portuguese - P...
- **146.0** — When the timepicker is enabled for `<input type="time">` and `<input type="datetime-local">`, it now provides full keyboard and assistive technology support. This update also improves the behavior of the time spin but... (bug 1802201)
- **147.0** — Fixed an issue that prevented some Windows users from selecting a tab when the cursor was at the top of the screen and the Firefox window was maximized. (bug 1993474)
- **147.0** — Fixed a bug that made HTTP/3 requests containing non-UTF-8 header values time out or fall back to HTTP/2 after a while. (bug 1999128)
- **147.0** — A draggable button can now be dragged if initiated from the button itself. (bug 568313)
- **147.0** — For Linux GNOME Mutter users, window and rendering surface sizes were updated to match the actual pixel grid so Firefox delivers sharp rendering on fractionally scaled displays regardless of the actual window size. (bug 2000769)
- **148.0** — Fixed an issue where a language pack could become disabled after a major update, causing Firefox to display in the wrong language. (bug 2006489)
- **148.0** — On Windows, dragging a downloaded image to Adobe Illustrator now correctly inserts the image instead of its URL. (bug 1987375)
- **149.0** — Increased robustness of HTTP/3 upload performance for unstable network conditions. (bug 1852924)
- **150.0** — Fixed an issue on macOS where, when macOS Lockdown mode is enabled, emoji characters are not displayed in web content. (bug 2005440)
- **151.0** — Improved color management for copied and pasted images on macOS. (bug 1396587)
- **151.0** — Fixed an issue on macOS where maximized Firefox windows could reopen on the wrong monitor after relaunching in multi-monitor setups. (bug 875858)
- **151.0** — Fixed incorrect screen resolution reporting to websites in multi-monitor setups. (bug 1525379)
- **152.0** — Improved dragging images from Firefox to the desktop or Finder on macOS — images now save reliably and land where you drop them. (bug 401380)
- **152.0** — In multiple monitor situations, the *About Firefox* window now more reliably opens on the display with the most recently used Firefox window. (bug 1868738)
- **152.0** — Fixed arrow-key text navigation and word selection commands that moved in the wrong direction in right-to-left text on macOS and Linux. (bug 1425483)
- **152.0** — Fixed an issue where the Paste option could be missing from context menus when editing content on sites such as Squarespace, LinkedIn, and eBay. (bug 2043569)
- **154.0** — Fixed PDF documents not being scaled correctly in print preview when the *Fit to Page Width* option is used. (bug 2055723)
- **154.0** — Fixed an issue where the Windows taskbar would appear in front of a full screen Picture-in-Picture window. (bug 2048501)
- **154.0** — Fixed an issue on macOS where closing a browser window could leave behind an invisible window that continued to capture mouse clicks over the area where it used to be. (bug 2015460)
- **154.0** — Fixed the Vertical Tabs sidebar staying visible in full screen mode. It now hides along with the toolbars and reappears when the cursor moves to the edge of the screen. (bug 1927457)
- **154.0** — Fixed an issue on Windows where an auto-hiding taskbar would not appear until the Firefox window had been minimized and restored after launch. (bug 1957069)

Read across them and a usable threshold falls out. The fixes that clear the bar are ones where **an everyday interaction was reliably broken for an identifiable group of users, and they could tell**. Concretely, the recurring shapes are:

- **Operating-system integration papercuts** — clipboard and drag-and-drop (image transparency lost on copy, dragging an image to another application inserting a URL instead), multi-monitor behavior (windows reopening on the wrong display, wrong resolution reported to sites), fractional display scaling.
- **Text input and navigation** — arrow-key movement going the wrong way in right-to-left text, Paste going missing from context menus on major sites.
- **Breakage with a clear population** — a language pack silently disabling after a major update, an input method crashing, emoji vanishing under Lockdown mode.
- **Protocol or performance work with a describable user effect** — HTTP/3 upload robustness on unstable networks, requests timing out on non-conforming headers. Note the contrast: these earn notes because the user-visible consequence is stateable, not because a benchmark moved.

What is **absent** is as informative: no performance micro-optimizations, no cosmetic or spacing corrections, no edge-case correctness fixes, and nothing whose description would require naming an internal component. Several notes are platform-scoped (macOS, Windows, Linux/GNOME) — narrow platform reach is not disqualifying when the breakage is severe.

## Which areas produce notes

Bugzilla component of every noted bug (456 of 456 resolved; the rest are security-restricted or no longer readable).

| Component | Notes |
|---|---:|
| Core :: DOM: Core & HTML | 22 |
| Core :: CSS Parsing and Computation | 19 |
| Core :: Widget: Cocoa | 17 |
| Core :: Audio/Video: Playback | 13 |
| Firefox :: PDF Viewer | 11 |
| Core :: Privacy: Anti-Tracking | 11 |
| Firefox :: Address Bar | 11 |
| Core :: Widget: Gtk | 10 |
| Core :: Graphics | 10 |
| Core :: DOM: Navigation | 9 |
| Firefox :: Settings UI | 9 |
| Firefox :: New Tab Page | 9 |
| Firefox :: Tabbed Browser | 8 |
| Core :: Widget: Win32 | 8 |
| DevTools :: Inspector | 7 |
| Core :: Networking: HTTP | 7 |
| Firefox :: Profile Backup | 7 |
| Core :: DOM: Networking | 6 |
| External Software Affecting Firefox :: Other | 6 |
| Firefox :: Sidebar | 6 |
| Core :: DOM: UI Events & Focus Handling | 6 |
| Toolkit :: Add-ons Manager | 5 |
| DevTools :: Netmonitor | 5 |
| Firefox :: Theme | 5 |
| Firefox :: Search | 5 |

Long tail: 140 distinct components for 456 notes. Notes cluster in the front end and in web-platform components, and are almost absent from build, test, and internal-infrastructure components — which is what the mechanical-noise filter in the scan encodes.

## The denominator (what does *not* get a note)

For each sampled version, every bug marked `fixed` for that version versus the notes that shipped with it.

| Version | Bugs fixed | Notes | Rate |
|---|---:|---:|---:|
| 153.0 | 2,719 | 30 | 1.10% |
| 152.0 | 2,912 | 23 | 0.79% |
| 151.0 | 2,513 | 29 | 1.15% |
| 150.0 | 2,446 | 26 | 1.06% |

Aggregate: **108 notes out of 10,590 fixed bugs = 1.02%.**

Highest note yield among components with at least 25 fixed bugs in the sampled versions (below that, one note reads as a huge percentage and means nothing):

| Component | Fixed | Noted | Rate |
|---|---:|---:|---:|
| Core :: Widget: Cocoa | 91 | 10 | 11.0% |
| Toolkit :: General | 27 | 2 | 7.4% |
| Firefox :: Profile Backup | 39 | 2 | 5.1% |
| Firefox :: PDF Viewer | 90 | 4 | 4.4% |
| Toolkit :: Form Autofill | 25 | 1 | 4.0% |
| Core :: DOM: Web Authentication | 26 | 1 | 3.8% |
| Core :: Graphics: Color Management | 28 | 1 | 3.6% |
| Core :: Privacy: Anti-Tracking | 90 | 3 | 3.3% |
| Core :: Layout: Positioned | 32 | 1 | 3.1% |
| DevTools :: Inspector | 33 | 1 | 3.0% |
| Core :: DOM: Web Serial | 33 | 1 | 3.0% |
| Core :: JavaScript: Internationalization API | 33 | 1 | 3.0% |
| Firefox :: Translations | 35 | 1 | 2.9% |
| Core :: WebRTC: Audio/Video | 38 | 1 | 2.6% |
| DevTools :: Netmonitor | 42 | 1 | 2.4% |
| DevTools :: General | 42 | 1 | 2.4% |
| Release Engineering :: Release Automation | 45 | 1 | 2.2% |
| Firefox :: Address Bar | 136 | 3 | 2.2% |
| Core :: Security: PSM | 47 | 1 | 2.1% |
| Firefox :: Tabbed Browser | 49 | 1 | 2.0% |

And the opposite end — the busiest components that produced **no** notes at all across four releases. This is where an unfiltered scan burns most of its effort:

| Component | Fixed | Noted |
|---|---:|---:|
| Testing :: web-platform-tests | 324 | 0 |
| Core :: Graphics: WebRender | 153 | 0 |
| Firefox for Android :: Homepage | 144 | 0 |
| Firefox for Android :: UI Tests | 137 | 0 |
| Firefox for Android :: Tooling | 134 | 0 |
| Firefox for Android :: General | 119 | 0 |
| Core :: SVG | 116 | 0 |
| Firefox for Android :: Browser Engine | 113 | 0 |
| Firefox for Android :: Experimentation and Telemetry | 110 | 0 |
| Firefox Build System :: General | 107 | 0 |
| Firefox for Android :: Privacy | 100 | 0 |
| Firefox for Android :: Tabs | 96 | 0 |
| Firefox Build System :: Task Configuration | 89 | 0 |
| Core :: JavaScript Engine: JIT | 89 | 0 |
| Developer Infrastructure :: Lint and Formatting | 87 | 0 |
| Firefox :: Messaging System | 86 | 0 |
| Core :: JavaScript: WebAssembly | 80 | 0 |
| Web Compatibility :: Site Reports | 78 | 0 |
| Core :: DOM: Security | 75 | 0 |
| Core :: WebRTC | 74 | 0 |

**Read this table with one caveat.** The scope here is Firefox / Release, so components belonging to a *different* product — the `Firefox for Android ::` entries above especially — are structurally zero: their bugs are flagged fixed in the same Gecko version but their notes ship in a different product's notes entirely. That is a scoping artifact, **not** evidence that mobile work is unnotable. The genuinely informative rows are the ones in products that *do* feed these notes: test suites, build and lint infrastructure, and engine internals (JIT, WebAssembly, WebRender, SVG) carry heavy fix volume and reliably produce nothing. Re-run with `--product 'Firefox for Android'` to calibrate mobile separately.

## Observations that bear on discovery

- **Bug numbers are recorded on 258 of 546 major-release notes (47%) versus 215 of 242 dot-release notes (89%).** Dot releases require bug links and mainline notes don't, which is exactly the gap you see. Nucleus keeps the bug number as a field even when the published note doesn't render a link — so this corpus can be joined to Bugzilla either way.
- **38 notes are marked as progressive rollouts.** A gated or staged feature does get noted; being behind a rollout is not by itself a reason to hold a note, but it changes the wording.
- **21 known-issue notes.** These recur across several versions, which is why per-release counting has to use note–release pairs rather than distinct notes.
- **The `HTML5` tag is still in live use** alongside `Developer`, and is what renders as the *Web Platform* heading -- there is no `Web Platform` tag. Web-platform notes therefore appear under more than one tag historically, so match a product's established usage rather than normalizing. That is not licence to leave a note in the wrong one of the two: see the style guide for the engine-versus-DevTools split that decides it.

## Provenance

- Source: `https://nucleus.mozilla.org/rna/notes/?format=json` and `https://nucleus.mozilla.org/rna/releases/?format=json` (public, unauthenticated).
- Scope: product `Firefox`, channel `Release`, released on or after `2024-08-01`.
- 84 releases in scope, 781 distinct public notes, 793 note–release pairs.
- Bugzilla REST used for component lookup and fixed-bug denominators.
- Generated by `scripts/relnotes/fetch-shipped-notes.py`; counts are as of the run date.

### Measuring this corpus yourself

Ad-hoc counts over the Nucleus payload drive calibration decisions, and each of these has produced a confident wrong answer:

- **Exclude `Community`** — the `NON_AUTHORED_TAGS` set in `fetch-shipped-notes.py`. Contributor credits are generated rather than team writing, so their markup, length and phrasing are not evidence of house practice. Leaving them in is what makes raw HTML links look like current convention.

- **`Enterprise` is excluded by text, not by tag, and only the old pointer.** Notes reading "You can find information about policy updates…" linked out to the separately maintained Firefox for Enterprise notes; those stopped being maintained in August 2026 and Release Management writes these notes now. So the tag is hand-authored going forward and belongs in the corpus, while its historical volume is almost entirely that one pointer. **Do not calibrate the enterprise bar off this corpus** — the class was documented elsewhere, not judged and rejected.

- **Markdown links come in two forms, and the reference form dominates.** Inline `[text](url)` is the one people write regexes for; `[text][1]` with a `[1]: url` definition below is what the notes actually use. Measured over notes authored since 2024: **1,135 reference-style against 19 inline**, so an inline-only pattern sees under 2% of them.

Together the two turn *zero* raw `<a href>` in team-authored notes into an apparent even split with Markdown. A count of *absence* is the kind most worth re-deriving before acting on it, because absence is what becomes a rule.
