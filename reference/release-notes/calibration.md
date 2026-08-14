# Calibration: what real passes got wrong

Empirical calibration for `find-release-note-candidates`, kept out of the skill body on purpose.
**Read it before tiering** — every item is a case where a pass was wrong and a Release Manager or
the tree corrected it, so it outranks intuition.

It lives here because it is an incident log, and an incident log grows one entry per mistake.
Inline it crowded out the process it was meant to inform: the skill states the method, this file
states what went wrong when the method was skipped. The same split already separates
[`style-guide.md`](style-guide.md) (the checklist) from the drafting notes in the skill.

## Measured coverage (Firefox 153 backtest)

Against the 27 bugs that earned a published 153.0 note:

| Basis | Recall |
|---|---|
| Nightly cycle only | 18/27 = **67%** |
| Nightly + beta cycle | 20/27 = **74%** |

The mechanical filter caused **zero** of the misses. The remaining 7 are structural, and knowing the
classes matters more than the number:

- **Beta uplifts** (2033733, 2053060) — landed on `main` *after* `FIREFOX_NIGHTLY_153_END`, then
  uplifted into 153's beta, so a scan of 153's nightly cycle never sees them.
- **Landed in an earlier cycle, noted later** (1996422, 2038877, JPEG XL 2016688) — the code shipped
  in 152's cycle; the note came when a preference flip or Labs offering followed.
- **Third-party upstream** (2032967, 2043290) — PDF Viewer features arrive via `Update PDF.js to
  <sha>` commits filed under *different* bug numbers, so the feature bug has no Gecko commit at all.
  Any commit-based scan is blind to these.
- **Open tracking bug** (2012848) — an open `[meta]` whose work landed long before the window.
- **Post-release** (1681745) — a known issue added after 153.0 shipped.

**So a git-window scan tops out around 74% for version coverage, and must say so.** For a
version-complete pass the authoritative set is Bugzilla `cf_status_firefox{N}=fixed`. Use git windows
for freshness work, and never claim version-completeness from one.

**A nightly-cycle scan alone cannot give version-complete coverage, and must not claim it.**
Work also lands during the **beta** cycle: a change can land on `main` during version N+1's nightly
cycle and be uplifted into version N's beta. Measured on the Firefox 153 cycle, 9 of the 27 bugs
that earned a 153.0 note were unreachable from the 153 nightly window — the same classes listed
above, with beta uplifts the largest.

So: **for freshness work (daily/weekly), scan the git window. For version-complete coverage before a
release, the authoritative set is Bugzilla `cf_status_firefox{N}=fixed`, not a git range.** State
which basis you used, and when you used a git window, say that uplifts and upstream-library work are
outside it.

## Calibration from real passes

Learned by comparing a `find-release-note-candidates` pass against a Release Manager's own pass on
the same build. Every item below is a case where the pass was wrong, so they carry more weight than
intuition.

**Signals that argue *against* a note:**

- **Crash fixes are essentially never mainline notes.** "I can't imagine many situations where we'd
  call out a crash fix in relnotes for a major release." Treat a crash fix as a drop unless it is
  both very frequent and has a describable user-facing trigger. On that basis alone it can be
  rejected — no impact analysis needed.
- **Check how old the *regressor* is, not just the bug.** `bug-detail.py` resolves `regressed_by`
  and reports the gap. Bug 2052660 (SVG `<use>`/`<symbol>`) looked strong — external reporter, a
  duplicate naming a real site — until the regressing change turned out to have landed in **2019**.
  Broken for six years with one duplicate is edge-case by evidence, whatever the symptom sounds
  like. Do this before weighing the site name.
- **A spec-conformance change with no describable user consequence is not a note** (bug 2053485,
  scroll direction for `unicode-bidi: plaintext`). "We now match the spec" is not user impact.
- **For a removal, the gate is the removed thing's default *before* removal — not how much code went
  away.** Deleting a feature that was off by default has zero user-facing delta no matter how many
  parts the removal has. Bug 2058143 removed cookie banner handling in ten parts (UI, about:prefs,
  Fenix, Focus, GeckoView, Remote Settings rules, plus a profile-data bump to clear user exceptions)
  and was still declined: `cookiebanners.service.mode` was **0** at both `FIREFOX_NIGHTLY_154_END` and
  `FIREFOX_BETA_153_BASE`, so nothing users had was taken away. Reading the commit log tells you the
  extent of the deletion, which is a different question — check the preference at a revision *before*
  the removal landed, since afterwards the pref is gone and `--lookup` cannot see it. Same shape as
  `svg.SVGAElement.text.enabled`, where the attribute was deleted while the pref was false.
- **Longstanding and unnoticed means low impact.** A bug that existed for years without anyone
  reporting it is edge-case *by evidence*, no matter how bad the symptom sounds. Bug 2050228 (page
  text missing from printouts) was rejected on exactly this: "it looks like a very edge-case bug
  given how long the bug has existed without anybody noticing it." Check the bug's age against
  whether anyone actually hit it.
- **A pending uplift request means the target version will move.** `approval-mozilla-* = ?` on the
  bug's attachments. Two of three rejections on the first compared build were partly "very likely to
  get uplifted." Don't file a note against Nightly N for something about to ship in N-1.
  **The flag is the late signal, not the first one** — read the bug's discussion too. Bug 2058432 was
  declined as "very likely to be uplifted per the comments in the bug" while no approval flag had
  been requested yet, so the scan's `PENDING UPLIFT REQUESTS` section said nothing. For any
  recent-regression candidate, `bug-detail.py <id> --comments` and look for anyone raising uplift,
  a dot release, or an affected shipped branch before proposing it.
- **No evidence of widespread impact.** A correct web-platform fix with no reports behind it isn't a
  note (bug 2052949, `modulepreload` event correctness).
- **A severe-sounding symptom on a niche configuration is still edge-case.** Bug 2059035 (invisible
  tab titles, address bar and menu text on KDE Plasma) reads alarming and was affected in shipped
  Release and Beta — and was still rejected as too edge-case. **A developer's opinion that it isn't
  an uplift candidate doesn't settle it either**; it may remain one. Weigh the size of the affected
  population, not how bad the symptom sounds in isolation.
- **Non-user-facing internals should be dropped, not tiered.** Refactors, IPC cleanups, telemetry,
  build fixes, stability assertions: "most look non user-facing which makes them extremely unlikely
  to be interesting." A Tier 3 full of internals is noise that costs the reader time — if it isn't
  user-facing, drop it with a reason.

- **Performance fixes are in the same boat as crash fixes** — not called out unless there's a bigger
  story to tell. And a measured win on an *internal Mozilla site* is not evidence of user impact
  (bug 2055399, a 21-second Treeherder stall).
- **A clarification that doesn't change behavior isn't a note.** Bug 2058276 corrected misleading
  Private Browsing text; the behaviour was always the same. Rewording to describe reality more
  accurately doesn't clear the bar.
- **A recent regression being uplifted is a net non-event.** Bug 2057970 broke in 154, was fixed in
  155, and the uplift was already on track — across shipped versions the user sees nothing. Two
  related shapes: a regression that never shipped at all (`fx153=unaffected fx154=unaffected`) needs
  no note either.
- **Check precedent before proposing.** `fetch-shipped-notes.py --search '<regex>'` greps every
  note ever published. "I don't believe we've called out past ffmpeg updates" was verifiable in
  seconds: exactly **one** note in the entire corpus mentions ffmpeg/libavcodec, and it was about
  *blocking* old versions. If a class of change has no precedent across two decades, that's strong
  evidence it isn't noted.
- **Precedent that exists does not carry a candidate over weak impact evidence.** The check above is
  strong evidence *against* when it comes back empty, and only weak evidence *for* when it comes back
  full — the asymmetry is the whole point, and reading it symmetrically is what produces the mistake.
  Declined three times on exactly this: bug 1804663 (RTL datetime-local field order; precedent 105.0.2
  and 152.0), bug 1969451 (contenteditable plaintext-only double-backspace; precedent 139.0 and
  136.0), bug 2061113 (VoiceOver and `aria-describedby`; precedent 92.0, 95.0, 93.0, 82.0). **All
  three had zero duplicates.** Settle impact evidence first and use precedent only to sanity-check a
  candidate that already earned its place.
- **That rule is scoped to *user-facing* candidates. Impact evidence is not the filter for a
  web-platform one.** All three declines above are user-visible bug fixes, and it holds there.
  Applied to a Web Platform candidate it rejects nearly everything that actually ships: of the 19
  Web Platform notes in the 155.0a1 set, **16 had zero duplicates and 17 had zero `see_also`**. The
  category is noted for a web-exposed behaviour having changed, not for anyone reporting it, so
  absent evidence is the norm rather than a signal. Judge these on whether web-exposed behaviour
  changed, on the gate, and on precedent. Bug 2055211 — the module map no longer caches HTTP errors,
  so a failed `import()` can be retried — was dropped from a 92-survivor day carrying
  `dupes=0 see_also=0 blocks=1`, a profile identical to nine of those 19.
- **A duplicate count is impact evidence only if the duplicates are user reports — read them, don't
  total them.** Bug 2058198 (container colour stroke spanning the tab) had the strongest-looking
  evidence in its window, an external reporter and 4 duplicates, and was still declined: three of the
  four are tagged `[Nova]` and it blocks meta 2052231 `[meta] Nova foxfooding bugs`, so the cluster is
  staff finding staff bugs on an unreleased redesign. `bug-detail.py` prints every duplicate's
  summary for this reason. A feature-team tag or a foxfooding meta in that list means the number
  overstates the reach.
- **Library and vendor bumps are dropped mechanically**, matched on the bug summary rather than the
  landings, because a big bump like `Update to libwebrtc 151` carries hundreds of commits and a few
  stray build fixes would otherwise keep the whole thing. They land in the dropped list, which the
  report links for auditing.
- **pdf.js version bumps: don't ping per update.** There can be several a week. Ignore them unless
  the commit log linked from the bug's comment 0 shows a clear user-facing change.

- **Filed internally with no duplicates is weak evidence, whatever the summary says.** This is the
  single most repeated correction. Bugs 1975797 ("download progress only updates if another byte
  arrives"), 2058585 (`view/scroll-timeline` on pseudo) and 1830603 (border-radius background
  clipping, an *old* bug with `parity-chrome` and `regression`) were all rejected on it. `daily-pass.py`
  now reports reporter domain, duplicate count, see_also and cc per survivor — **read that before
  tiering.** Keywords, bug age and a severe-sounding title are not impact evidence; outside reporters
  and duplicates are.
- **A severe recent regression predicts an *uplift*, not a note.** `fx153=affected fx154=affected` on
  something that broke recently means it will likely be fixed on those branches, leaving no
  user-visible delta to describe. Bug 2054991 (unrecoverable certificate error) was proposed partly
  *because* both channels were affected — that reasoning is backwards.
- **Don't propose individual work items from a feature still under development.** The Vulkan video
  decoding bugs are pieces of unshipped work; individual fixes within it aren't notes. Applies even
  when the bugs don't cluster — judge the *area's* maturity, not just the bug.
- **Changes delivered by a train-hop system add-on are not release-note candidates.** New Tab Page
  work ships through the newtab system add-on, which rolls out to **all users outside the normal
  release cycle**. There is no Firefox version the change "arrives in", so there is nothing to note
  against. Bug 2046143 (the New Tab weather widget activating without opt-in) was rejected on this
  despite being a genuine privacy fix with an outside reporter.
  **The version flags actively mislead here:** it read `fx153=wontfix fx154=wontfix fx155=fixed`,
  which describes the Gecko tree, not when users get the fix. Treat `Firefox :: New Tab Page` — and
  any other component you know ships as a train-hop add-on — as delivered out-of-band, and check the
  bug for uplift comments mentioning trainhop before proposing anything.
- **Weigh the surface's own reach, not just the change.** A correct improvement to a **niche, recently
  shipped** surface is not a note on its own merits — bug 2040379 (Web Serial parity-error
  detection) was rejected because Web Serial itself only recently reached stable and has a small
  audience. Ask how many people use the surface before asking how good the change is. **This applies
  to UI surfaces as much as to web APIs**: scoped to APIs, the rule reads past a candidate like bug
  2051292 (long-press an Android toolbar shortcut to edit it), which went out with reservations.
- **A change to an internal configuration surface is not a note unless the surface is being
  replaced.** `about:config` and its neighbours are deliberately not promoted, whatever their
  traffic — that is policy rather than audience size, so the reach test above does not reach it.
  Measured across the shipped corpus: `about:config` appears in 16 notes in total and is the
  *subject* of only two, both the same Firefox 71 change ("Configuration page reimplemented in
  HTML"); nearly all the rest name it as the place to flip a preference for some other feature. Bug
  449178 (treat a spaced about:config filter as a multi-term search) was ungated and genuinely
  user-visible, cleared every other filter here, and was declined on this ground alone.
- **"Now configurable via a preference" is not a note.** Notes describe default behaviour; anything
  requiring a manual `about:config` change is out of scope (bug 1418178, Ctrl-Tab preview count).
  Check whether the *default* actually changed before treating a new preference as a candidate — in
  1418178 the value had been hardcoded at 7 and the preference kept it at 7, so nothing changed for
  anyone. A `pref-delta.py` line reading `None -> 7` means the preference is new, **not** that the
  behaviour is.
- **A fix for a Nightly-only or unshipped feature is not a note.** The broken behaviour never reached
  users (bug 2045680, a fix to a Nightly-only Android feature). **For any fix, identify the feature
  it belongs to and look up that feature's preference before proposing it** — not just the bug's own
  gating. Bug 2022210 (HTML color picker popup) looked like a solid external-reporter fix until
  `dom.forms.html_color_picker.enabled` turned out to be Nightly-only. One `pref-delta.py --lookup`
  would have caught it, and this class shouldn't be flagged at all.

**Two ways context can wrongly *suppress* a candidate — both cost real asks:**

- **A watchlist entry is not an ask.** Bug 2056188 (`[155 early access] Enable browser.nova.enabled
  for Nightly only`) was skipped because a `nova` feature entry already sat on the watchlist with
  status `nightly-note-requested`. But nobody had pinged *that bug*. A tracked feature suppresses
  **re-asking the same bug**; it must not suppress the bug that actually enables the feature. Ask
  the question a fresh session would ask.
- **A cluster marked "hold" should not silently bury a notable member — but the cluster context is
  what decides.** Bug 1814460 (`Implement RTCError for DataChannel/sctp failures`) sat inside the
  Interop 2026 WebRTC rollup, whose completeness check said hold, and went unmentioned. Raised on
  its own it looked worth asking; once the rollup context was visible the call was to **defer and
  revisit at the end of the Nightly cycle for a single rollup note**. So: surface the member *with*
  its cluster and completeness state, and let the owner choose between an individual ask and a
  cycle-end rollup. Don't hide it, and don't reflexively ask about it either.

**Signals that argue *for* a note:**

- **Duplicate or variant reports hanging off the bug.** Read what the `blocks`/`depends_on`/`see_also`
  bugs *are*, not how many there are — `bug-detail.py` resolves them to summaries for this reason.
  A fix can look minor while the bug it blocks is the long-standing annoyance users actually filed
  about: bug 2056362 read as a modest Android autofill tweak until its blocker, bug 2040184 (itself
  with a duplicate), showed what it partially addresses. Bug 2051354 was a borderline call that tipped to "ask" because
  it blocks two independent user reports of the same data loss (`[Linux/Wayland] Data loss in M365
  Word Online: Diacritic keys (å,ä,ö)…` and `sharepoint.com - Some part of the text disappears…`).
  Raw counts don't discriminate — bug 1452337 had *more* links and was still rejected.
- **A big site by name.** Narrow technical reproduction plus a major site (Microsoft 365, SharePoint)
  can outweigh "only some environments."
- **A new user-visible capability, even a small one** (bug 1477920, a Developer Tools toggle).
- **A public bug describing a widely-known product shortcoming**, even when it carries security
  keywords. Bug 1699444 (Android tabs opened by extensions *displayed* as private without actually
  being private) has `sec-moderate` and `csectype-disclosure` — and is still worth asking about: it
  is a public bug with three duplicates and a long discussion. **Security keywords do not
  automatically mean "covered by the security advisory".** Weigh whether the *user-visible*
  behaviour is a known shortcoming worth telling people was fixed.

**Judge the landing, not the bug summary — this rule has already been broken once.** Bug 267369 is a
2004 feature request ("put source URL into the saved file's properties") that was proposed as a
Tier 1 candidate. Its **only** landing was `Document how Firefox records download origin metadata …
DONTBUILD` — documentation, no code. An old bug suddenly showing activity is very often cleanup or
docs, not the feature finally arriving. `DONTBUILD` and leading `Document…` are now mechanical
drops, but the habit matters more than the patterns.

**The reference outcome:** of 29 survivors on build 20260731085738, the Release Manager took **three**
to ask developers about — 1477920, 2051354, 2058840. The skill had proposed two of those in Tier 1
and one in Tier 2, plus four Tier 2 items and seven Tier 3 items that were all rejected. Erring
toward inclusion is still right, but **internals and crash fixes are dead weight, not cheap
over-inclusion.**

