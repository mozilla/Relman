# Tiering card: the per-pass digest

What a pass applies, distilled from [`shipped-notes-survey.md`](shipped-notes-survey.md) (the bar),
[`calibration.md`](calibration.md) (every rule here is a case a pass got wrong) and
[`style-guide.md`](style-guide.md) (drafting). Read this every pass; open those files for the case
behind a rule, the measurements, or anything this card does not settle. When the card and a full
file disagree, the full file wins and the card needs fixing.

## The bar

- About 1% of a major's fixed bugs earn a note (the survey has the current figures). That is a
  publication rate, not a pruning budget: err toward asking, and let the tier carry the doubt.
- `Fixed` notes in majors are rising fast, so "fixes don't get mainline notes" is outdated. What
  clears it: an everyday interaction reliably broken for an identifiable group, and they could tell.
  OS integration papercuts (clipboard, drag and drop, multi-monitor, scaling), text input and
  navigation, breakage with a clear population, protocol work with a stateable user effect.
- Never noted: performance micro-wins, cosmetic or spacing fixes, edge-case correctness, anything
  that needs an internal component named to describe it. Platform-scoped is fine when the breakage
  is severe.
- Busy components that produce nothing, e.g. test suites, build and lint, JIT, WebAssembly,
  WebRender, SVG; the survey's zero-yield table is the current list.
- Easy to forget: new locales and system-requirements changes rarely look like features.

## Signals against

- Crash fixes and performance fixes: not notes without a bigger story. A win on an internal Mozilla
  site is not user impact.
- Filed internally with no duplicates is weak evidence, whatever the summary says. `EXTERNAL` is a
  domain test: a developer on a personal address (the assignee, the area owner) is not outside
  evidence.
- Read the duplicates, don't count them: feature-team tags or a foxfooding meta mean staff found it.
- Old regressor or longstanding bug with few reports: edge case by evidence.
- A severe symptom on a niche configuration (one desktop environment, one driver) is still edge
  case; a developer saying it is not an uplift candidate does not settle it either.
- Judge the landing, not the summary: an old bug suddenly active is often docs or cleanup.
- pdf.js bumps (in the drop list): rescue one only if its linked commit log shows a user-facing
  change.
- Recent severe regression, or a pending or discussed uplift: predicts an uplift, not a note. Read
  the comments; the approval flag is the late signal. A regression that never shipped needs no note.
- Fix to a Nightly-only or unshipped feature: resolve the feature's gate, not just the bug's.
- Removal: the gate is the removed thing's default before removal, not how much code went away.
- "Now configurable via a preference", `about:config` changes, rewording that matches existing
  behaviour, spec conformance with no describable consequence: not notes.
- Niche surface (recently shipped API, small UI audience): weigh the surface's reach first.
- Train-hop New Tab work ships out of band: no version to note against, whatever the flags say.
- Individual pieces of unshipped feature work (Vulkan video and the like) are not notes.
- Precedent that exists does not rescue weak impact evidence; precedent that is absent is strong
  evidence against. Check with `fetch-shipped-notes.py --search`.

## Signals for

- Blockers or see-also that are independent user reports of the same problem; read what they are.
- A big site by name (Microsoft 365, Google, major retailers) can outweigh "only some environments".
- A new user-visible capability, even a small one.
- A public, widely known shortcoming, even with security keywords.
- Web Platform is judged differently: zero duplicates is the norm. Ask whether web-exposed behaviour
  changed, what the gate is (often older than the change, in `dom/webidl`), and what the precedent
  is.
- A flip marked `NIGHTLY NOTE ENDS` is Tier 1 with no fresh significance call.

## Context that must not suppress a candidate

- A watchlist entry for a feature is not an ask on the bug that enables it.
- A held cluster does not bury a notable member: surface it with the cluster's state, let the owner
  pick.

## Tiers

Tier 1 verified and clearly user-facing; Tier 2 plausible but unconfirmed, saying what is
unconfirmed; Tier 3 almost never (no Tier 3 item has ever been accepted). Internals are dropped with
a reason, never tiered. Gated off everywhere goes to the watchlist, recorded with `--gate`.

## Drafting checklist (run over every draft)

- Symptom the user saw, not the mechanism; cut the cause clause. About 20 words.
- `Fixed` leads with a past-tense verb; `New` and `Changed` are present tense ("X now does Y").
- No "you"; write impersonally.
- Spell out abbreviations: "preference", "Developer Tools". Inline `code` for API, CSS and HTML
  names.
- Full stop at the end. One line, no hard wrap.
- No preference names in a note that rides the trains; a Nightly note may name one.
- Platform scope only after checking comment 0, see-also and the version flags, not the summary. Two
  platforms in play: drop the platform lead.
- "X is Y when it shouldn't be" can resolve either way: read the patch for the direction.
- Scope you inferred is stated loosely or flagged, never precisely.
- Nightly note: "Starting with Firefox N" citing the version it first shipped in, and say Nightly in
  the sentence. Android in a shared Nightly or Beta set: lead with "On Firefox for Android".
- Tag: `HTML5` (Web Platform) for what the engine ships to web content; `Developer` for the tools.
- Naming a site users recognise is good practice.
