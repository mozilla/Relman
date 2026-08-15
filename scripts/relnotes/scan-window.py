#!/usr/bin/env python3
"""Enumerate a landing window on firefox-main and funnel it down to release-note candidates.

This is the deterministic front half of release-note discovery. Doing it in context does not
scale: a day is ~300 commits, a full nightly cycle is 8,000+. Everything here is mechanical and
auditable, so the model only spends attention on survivors.

The funnel:

  commits in window
    -> bug ids (first `Bug NNNNNNN` in each subject)
    -> Bugzilla batch fetch (status, component, keywords, type, whiteboard, dependencies)
    -> keep only currently FIXED           (the real backout filter; see below)
    -> drop mechanically-never-noteworthy  (component and subject heuristics)
    -> survivors

**Backouts.** On the git mirror these are `Revert "..."`, not the Mercurial "Backed out" -- matching
only the latter finds nothing. But revert text does not decide what is dead: things land, get backed
out, and re-land inside one window. The authoritative signal is the Bugzilla status, so reverts are
recorded as a flag to double-check rather than used as a filter.

**The component drop list is empirically derived**, not guessed -- see the zero-yield table in
reference/release-notes/shipped-notes-survey.md, where components like Testing :: web-platform-tests
carry hundreds of fixed bugs across four releases and produce no notes at all.

Usage:
  scan-window.py --since-last
  scan-window.py --range FIREFOX_152_0_RELEASE..FIREFOX_153_0_RELEASE --format json -o /tmp/w.json
  scan-window.py --build 20260803160643 --show-dropped
"""

import argparse
import collections
import json
import re
import sys
import urllib.parse as url_parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

BUGZILLA_REST = "https://bugzilla.mozilla.org/rest/bug"
# What `limit=0` actually returns at most, measured 2026-08-14. The response carries no total and no
# truncation marker, so hitting this is indistinguishable from a complete answer unless counted.
BUGZILLA_MAX_RESULTS = 10000
# Bugzilla's answer to "did this land in N". Shared by is_fixed() and the --census search, so
# the query cannot drift from the predicate.
LANDED_IN_VERSION = ("fixed", "verified", "disabled")
PRODUCT_DETAILS = "https://product-details.mozilla.org/1.0/firefox_versions.json"
USER_AGENT = "Relman-relnotes-scan/1.0"

BUG_RE = re.compile(r"\b[Bb]ug\s+(\d{5,8})\b")
REVERT_RE = re.compile(r"^\s*(Revert\b|Backed out\b|backout\b)", re.IGNORECASE)

# Products/components that reliably produce nothing. Grounded in the survey's
# zero-yield analysis rather than intuition.
#
# "Reliably" is not "never", and this was measured rather than assumed: checking all 3,306 noted bugs
# in the Nucleus corpus against these predicates finds 69 published notes that this list would drop,
# 10 of them in Firefox 140 or newer. They cluster in `Firefox Build System :: General`,
# `Release Engineering :: General` and `:: Release Automation`, and they are packaging, installer,
# update-staging and platform-support changes -- bug 2058594 (partial-update crash, 153.0.3),
# bug 1987132 (32-bit Linux support dropped, 145) and bug 213920 (.rpm packages, 149-151).
#
# **Deliberately left as-is, by Release Management, 2026-08-11.** A release-note-worthy change from
# these products is rare enough that the developer nominates the bug themselves without prompting, so
# the flag queue catches it: relnote-flag.py applies none of these filters, which makes `--nominated`
# and `--coverage` a complete path for exactly this case. Widening the funnel instead would admit ~98
# bugs per cycle, mostly from components that have never produced a note (Task Configuration,
# Toolchains, Bootstrap Configuration, Mach Core). Do not re-propose narrowing this to the component
# level without new evidence -- the measurement above is the answer, not an oversight.
DROP_PRODUCTS = {
    "Testing",
    "Firefox Build System",
    "Developer Infrastructure",
    "Release Engineering",
    "Conduit",
    "Tree Management",
    "Infrastructure & Operations",
    "Data Platform and Tools",
    "bugzilla.mozilla.org",
    "Socorro",
    "Taskcluster",
}
DROP_COMPONENT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^UI Tests$",
        r"^Tooling$",
        r"\bTest",
        r"^Lint and Formatting$",
        r"^Task Configuration$",
        r"^Experimentation and Telemetry$",
        r"^Telemetry$",
        r"^Documentation$",
        r"^General Automation$",
    )
]

# Library/vendor bumps. Matched against the BUG SUMMARY and decisive on their own,
# unlike the subject patterns below which require *every* landing to look mechanical.
# That all-must-match rule fails badly here: bug 2049872 ("Update to libwebrtc 151")
# carried 251 landings, and a handful of build fixes among the "Vendor libwebrtc from
# <sha>" commits was enough to keep the whole thing. Library bumps are not release
# notes; when one carries a user-facing change (pdf.js occasionally does), it is caught
# by reading the commit log linked from comment 0, not by the funnel.
LIBRARY_BUMP_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^\s*Update .+ to new version\b",
        r"^\s*Update to lib\w+",
        r"^\s*Up(date|grade) \S+ to (new version|v?\d)",
        r"^\s*Update \S+ to \S+ from \d{4}-\d{2}-\d{2}",
        r"^\s*Vendor \S+ from\b",
        r"^\s*Update (libwebrtc|libvpx|libyuv|irregexp|cubeb|PDF\.js|jxl-rs|"
        r"application-services|ICU|NSS|NSPR|zlib|harfbuzz|freetype|dav1d|aom)\b",
    )
]

# Subject shapes that are structurally never release notes.
DROP_SUBJECT_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(crashtest|reftest|wpt|web-platform-test|mochitest|xpcshell|gtest)\b",
        # Test work described with any mechanical verb, in either order.
        r"\b(add|added|adding|expand|expanded|update|updated|fix|fixed|skip|skipped|enable|"
        r"enabled|disable|disabled|re-?enable|remove|removed|migrate|port|convert|split|"
        r"deflake|unskip)\b[^.]{0,40}\btests?\b",
        r"\btests?\b[^.]{0,40}\b(await|fail|failing|pass|passing|flaky|intermittent|hang|"
        r"timeout|timing out|leak)\b",
        r"\btest (fixtures?|harness|infrastructure|utilit|helper)",
        r"\bfixtures?\b",
        r"\bmark .* (passing|failing|expected|intermittent)\b",
        r"\btest[- ]only\b",
        r"\bupdate (the )?expectations?\b",
        # Telemetry and measurement plumbing: never user-facing on its own.
        # `probe`/`ping` only count when a word directly precedes them, which is how genuine
        # telemetry churn reads ("legacy probe", "newtab ping", "socket thread probes"). Used as the
        # landing's leading verb it means something else entirely: bug 2033325 ("(part 1) Probe
        # backup directory access to trigger permission prompts") earned a published note and every
        # one of its five landings was dropped by this pattern.
        r"\b(telemetry|glean|scalar|histogram)s?\b|\b\w+\s+(ping|probe)s?\b",
        r"\bmetrics?\.yaml\b",
        # Explicit infrastructure markers teams put in bug summaries.
        r"\[[^\]]*\bInfra\b[^\]]*\]",
        r"\b(CI|taskcluster|try server|treeherder)\b",
        r"\bvendor(ing|ed)?\b",
        r"\bupdate .*\b(to version|to v?\d+\.\d+)",
        r"\bbump\b.*\bversion\b",
        r"\bcargo (update|vet|audit)\b",
        r"\bBUG_COMPONENT\b",
        r"\b(update|add|fix) (the )?docs?\b",
        r"\bdocumentation\b",
        # "Document how X works" is a docs-only landing; the leading verb form is not
        # caught by the patterns above. Real miss: bug 267369, whose ONLY landing was
        # "Document how Firefox records download origin metadata ... DONTBUILD", read as
        # a 20-year-old feature finally shipping.
        r"^\s*Document(ing)?\b",
        # DONTBUILD means the change cannot affect a build, so it cannot be user-facing.
        r"\bDONTBUILD\b",
        r"\bstring(s)? (update|import|freeze)\b",
        # `l10n` alone matched a code fix that merely touched l10n sources: bug 2049845 ("Stop
        # AboutNewTabResourceMapping from re-entering its own observer when registering shadow l10n
        # sources") is the 152.0.2 startup-freeze regression and earned a published note. Require
        # the l10n word to name the landing's action.
        r"\b(l10n|localization)\s+(import|update|sync|bump|string|landing)|"
        r"\blocale import\b|\bstring import\b",
        # Bare `format` matched it as a NOUN and dropped real work: bug 2010411 ("Patch Gregorian
        # Hv format in en locale") earned a published note. Genuine formatting churn names its tool
        # or says reformat/formatting. `clang` stays bare so clang-tidy landings still drop.
        r"\b(clang|rustfmt|ruff|eslint|prettier|lint|reformat|formatting)\b",
        r"\bremove (dead|unused|obsolete)\b",
        # The refactor verbs must LEAD the subject, after the prefixes real subjects carry
        # ("Bug N -", "[tag]", "part 3", "Pre 1:", "Phase 6:", "Coverity CID N:", "wpt PR N").
        # Unanchored, these words match inside feature names and drop real candidates: bug 2047880
        # ("...new downloads dialog (that allows to rename and set...)") earned a *published note*
        # and was dropped by `rename`; bug 2058329 was dropped because "Monitor inline chat message"
        # contains `inline`; bug 2041996 by "Move tooltip-label word-wrap to xul.css" five days
        # running. Measured over FIREFOX_NIGHTLY_147_END..main (44,729 subjects, 22,442 bugs):
        # anchoring drops 113 fewer bugs (~16 per cycle) and removes one of the four proven false
        # drops among the 304 noted bugs in that range. Of an 18-bug sample of the newly-surviving
        # ones, ~7 were user-facing dialog/UI work this pattern had been hiding.
        r"^\s*(?:(?:bug\s+\d+|\[[^\]]{0,40}\]|\(?part\s+\d+\)?|pre\s+\d+|phase\s+\d+|"
        r"coverity\s+cid\s+\d+|wpt\s+pr\s+\d+|follow-?up)\s*[-\u2013\u2014:,.]?\s*)*"
        # The tail is lowercase-only and case-sensitively so, which admits the inflections
        # (renamed, moved, refactoring) while refusing to run on into a CamelCase identifier that
        # merely starts with one of the verbs. `RenameAndChangeLocation dialog should have the file
        # extension non-editable` (bug 2047953) is a UI change, not a rename. Measured over
        # FIREFOX_NIGHTLY_147_END..main, 48,235 subjects on 2026-08-14: the tail flips that one
        # subject and nothing else, 1,409 matches to 1,408.
        r"(rename|move|extract|inline|deduplicate|refactor)(?-i:[a-z]*)\b",
        r"\bno bug\b",
        r"\btypo\b",
        r"\bclean ?up\b",
        r"\bMerge (firefox-)?(autoland|beta|release|main)\b",
        r"^Backed out\b|^Revert\b",
    )
]


def fetch_json(url: str):
    """Thin wrapper over trainlib.fetch_json that exits with a CLI-friendly message.

    trainlib raises so importers can decide; the command-line tools all want to stop.
    """
    try:
        return trainlib.fetch_json(url)
    except RuntimeError as e:
        sys.exit(f"error: {e}")


def git(repo: Path, *args: str) -> str:
    try:
        return trainlib.git(repo, *args)
    except RuntimeError as e:
        sys.exit(f"error: {e}")


def nightly_version() -> int:
    data = fetch_json(PRODUCT_DETAILS)
    m = re.match(r"(\d+)", data["FIREFOX_NIGHTLY"])
    if not m:
        sys.exit("error: could not parse FIREFOX_NIGHTLY from product-details")
    return int(m.group(1))


def esr_versions() -> list[int]:
    """Live ESR majors, for uplift detection."""
    data = fetch_json(PRODUCT_DETAILS)
    out = []
    for key in ("FIREFOX_ESR", "FIREFOX_ESR_NEXT"):
        m = re.match(r"(\d+)", data.get(key) or "")
        if m:
            out.append(int(m.group(1)))
    return sorted(set(out))


def enumerate_commits(repo: Path, start: str, end: str, first_parent: bool = False) -> list[dict]:
    extra = ["--first-parent"] if first_parent else []
    log = git(repo, "log", f"{start}..{end}", *extra, "--format=%H%x09%s")
    commits = []
    for line in log.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        m = BUG_RE.search(subject)
        commits.append({
            "sha": sha[:12],
            "subject": subject,
            "bug": m.group(1) if m else None,
            "is_revert": bool(REVERT_RE.match(subject)),
        })
    return commits


def fetch_bugs(
    bug_ids: list[str], nightly: int, esrs: list[int] | None = None
) -> tuple[dict[str, dict], list[str]]:
    # Also pull the two earlier trains and ESR so uplifts are visible: a change that
    # lands on Nightly N but is uplifted to N-1 ships to users *sooner*, and its note
    # belongs to N-1, not N.
    version_fields = [f"cf_status_firefox{v}" for v in (nightly, nightly - 1, nightly - 2)]
    version_fields += [f"cf_status_firefox_esr{v}" for v in (esrs or [])]
    fields = ",".join([
        "id", "summary", "status", "resolution", "product", "component", "keywords",
        "type", "whiteboard", "blocks", "depends_on", "priority", "severity",
        *version_fields,
    ])
    out: dict[str, dict] = {}
    ids = list(bug_ids)
    for i in range(0, len(ids), 120):
        batch = ids[i:i + 120]
        qs = url_parse.urlencode({"id": ",".join(batch), "include_fields": fields})
        payload = fetch_json(f"{BUGZILLA_REST}?{qs}")
        for bug in payload.get("bugs", []):
            out[str(bug["id"])] = bug
        print(f"# fetched {min(i + 120, len(ids))}/{len(ids)} bugs", file=sys.stderr)
    missing = [b for b in ids if b not in out]
    return out, missing


def is_fixed(bug: dict, nightly: int) -> bool:
    """Did this bug's work actually land for this version?

    The per-version flag outranks the bug's overall resolution, which is the opposite
    of the intuitive order. Real cases from the 153 backtest: bug 1463402 (Picture-in-
    Picture Web API) is REOPENED with no resolution, and bug 2012848 is an open [meta]
    bug -- yet both carry cf_status_firefox153=fixed and both shipped release notes in
    153. Gating on resolution first drops them. A bug can be reopened for follow-up
    work, or be a tracking bug that stays open, long after the shipped version's work
    is done.
    """
    flag = bug.get(f"cf_status_firefox{nightly}")
    # `disabled` counts as landed: the code is in, behind an off-by-default pref.
    if flag in LANDED_IN_VERSION:
        return True
    if flag in ("wontfix", "disabled-by-default", "unaffected"):
        return False
    # Flag unset (older bug, or the flag simply isn't used) -- fall back to resolution.
    return bug.get("resolution") == "FIXED" and bug.get("status") in ("RESOLVED", "VERIFIED")


SHIPPED_FLAGS = ("fixed", "verified")


def note_target(bug: dict, nightly: int, esrs: list[int]) -> dict:
    """Which version's release notes should this change appear in?

    Normally the Nightly version N. But a change uplifted to Beta (N-1) or Release
    (N-2) reaches users in *that* version first, so the note belongs there instead --
    and those ship sooner, which makes it time-critical rather than a curiosity.

    Uplift is not knowable when the change first lands: approval comes days to weeks
    later. So this has to be re-checked before notes are finalized, not just at
    discovery time.
    """
    shipped_in = []
    for v in (nightly - 2, nightly - 1, nightly):
        if bug.get(f"cf_status_firefox{v}") in SHIPPED_FLAGS:
            shipped_in.append(v)
    esr_hits = [v for v in esrs if bug.get(f"cf_status_firefox_esr{v}") in SHIPPED_FLAGS]
    earliest = shipped_in[0] if shipped_in else nightly
    return {
        "note_version": earliest,
        "uplifted": earliest < nightly,
        "shipped_in": shipped_in,
        "esr": esr_hits,
    }


# Commit subjects carry a reviewer list (`r=alice,android-l10n-reviewers`). Matching
# content patterns against it is a silent false-drop generator: bug 2047027 (an Android
# tab-ungrouping menu item) was dropped as localization work purely because
# `android-l10n-reviewers` reviewed it. Strip the suffix before classifying.
REVIEWER_SUFFIX_RE = re.compile(r"\s+r[=?][\w#,.@!\- ]+$")


def strip_reviewers(subject: str) -> str:
    return REVIEWER_SUFFIX_RE.sub("", subject).strip()


def tidy_subject(subject: str) -> str:
    """A landing subject without the redundant `Bug NNNN - ` prefix or the reviewer suffix.

    Handles `Bug N - `, `Bug N: ` and the `Bug N p2 - ` part convention.
    """
    subject = re.sub(r"^\s*[Bb]ug\s+\d+\s*(?:[pP](?:art)?\s*\d+)?\s*[-:.]\s*", "", subject)
    return strip_reviewers(subject)


# Capped, because a cycle pass reaches ~330 not-FIXED bugs and an uncapped line is a wall nobody
# reads. The count comes first and the remainder is named, so nothing vanishes without saying so.
def capped(items: list[str], limit: int = 40) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f", ... and {len(items) - limit} more (see scan.json)"
                    if len(items) > limit else "")


def render_dropped(dropped: list[dict]) -> list[str]:
    """The mechanical drop list, grouped by reason, as lines.

    Grouped rather than flat because a cycle pass drops over a thousand: a flat list of those is
    what "audited all 1145" was claimed against when a third had never been displayed. Grouping
    gives a summary that fits on a screen, lets the audit run per category, and stops every entry
    repeating a reason string that runs past 200 characters because it embeds the regex.

    Each entry shows its landings, not only its summary. The drop was decided on the landings and
    deliberately not on the summary, so showing the summary alone hands the auditor the field the
    decision ignored: `Group all media simulation UI` reads user-facing until you see that its one
    landing is `Renamed "simulation" to "emulation" for media emulations`.
    """
    by_reason: dict[str, list] = {}
    for d in dropped:
        by_reason.setdefault(d["drop_reason"], []).append(d)
    order = sorted(by_reason.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    lines = [f"Dropped as mechanical ({len(dropped)}):", "",
             f"SHAPE -- {len(order)} distinct reason(s). These counts sum to the total above; "
             "reconcile that against the funnel before calling the audit done."]
    for reason, items in order:
        # Truncated here only: a subject reason embeds its regex and runs past 200 characters,
        # which makes the shape table unreadable. Each group below repeats the reason in full.
        short = reason if len(reason) <= 104 else reason[:104] + "..."
        lines.append(f"  {len(items):>5}  {short}")
    lines.append("")
    for reason, items in order:
        lines.append(f"== {len(items)} dropped: {reason}")
        for d in items:
            lines.append(f"     {d['bug']}  {d['summary'][:90]}")
            landings = [c for c in d.get("landings", []) if not REVERT_RE.match(c["subject"])]
            for c in landings[:2]:
                lines.append(f"              landed: {tidy_subject(c['subject'])[:88]}")
            if len(landings) > 2:
                lines.append(f"              ... and {len(landings) - 2} more landings")
        lines.append("")
    return lines


def render_census(c: dict) -> list[str]:
    """The Bugzilla-side coverage check as lines.

    One renderer for the inline listing and --census-out, on the same reasoning as
    render_dropped: the file a pass re-reads afterwards must not drift from what it printed.
    """
    lines: list[str] = []
    lines.append(
        f"Bugzilla census (cf_status_firefox{c['version']} in "
        f"{'/'.join(c['flags'])}): {c['flagged']} flagged, {c['in_window']} in this "
        f"window, {c['outside_window']} not -- of those, {c['filtered_mechanical']} "
        f"mechanical, {c['earlier_version']} belong to an earlier version, "
        f"{c['flag_only']} have no landing of their own, "
        f"{c['to_review']} to look at."
    )
    if c["truncated"]:
        lines.append(f"  *** the flagged set hit Bugzilla's {BUGZILLA_MAX_RESULTS}-result "
                     "cap, so it is short and these gaps are UNDER-reported")
    if c["unfetchable"]:
        lines.append(f"  *** searchable but not fetchable ({len(c['unfetchable'])}): "
                     + capped(c["unfetchable"])
                     + " -- excluded from the four counts above, which therefore do not sum")
    if c["candidates"]:
        lines.append("")
        lines.append("Landed on a ref this window does not cover -- check for a beta uplift:")
        if c["cycle_in_progress"]:
            lines.append("  NOTE: this cycle has not merged, so most of these are on autoland ahead "
                         "of main rather than missed. Run the census after merge day.")
    for r in c["candidates"]:
        tag = f"  [also ESR {', '.join(str(v) for v in r['esr'])}]" if r["esr"] else ""
        lines.append(f"  {r['bug']}  {r['product']} :: {r['component']}"
                     f"  flag={r['status_flag']}{tag}")
        lines.append(f"        bug: {r['summary'][:140]}")
        for s in r["landings"][:3]:
            lines.append(f"        landed: {tidy_subject(s)[:140]}")
        if len(r["landings"]) > 3:
            lines.append(f"        ... and {len(r['landings']) - 3} more landings")
    if c["earlier"]:
        lines.append("")
        lines.append(f"Flagged for an earlier version too ({len(c['earlier'])}) -- usually QA "
                     f"verifying on {c['version']} a fix that landed before it, so the note, "
                     "if any, belongs to that version:")
        for r in c["earlier"]:
            lines.append(f"  {r['bug']}  -> {r['note_version']}  "
                         f"{r['product']} :: {r['component']}  {r['summary'][:90]}")
    if c["no_landing"]:
        lines.append("")
        lines.append(f"Flagged fixed in {c['version']} with no landing of their own "
                     f"({len(c['no_landing'])}) -- the flag was set without one, or the only "
                     "commit naming the bug is a backout blaming it:")
        for r in c["no_landing"]:
            lines.append(f"  {r['bug']}  {r['product']} :: {r['component']}"
                         f"  {r['summary'][:100]}")
    return lines


def drop_reason(bug: dict, subjects: list[str]) -> str | None:
    product = bug.get("product", "")
    component = bug.get("component", "")
    summary = bug.get("summary", "") or ""
    for pat in LIBRARY_BUMP_PATTERNS:
        if pat.search(summary):
            return "library/vendor bump"
    if product in DROP_PRODUCTS:
        return f"internal product ({product})"
    for pat in DROP_COMPONENT_PATTERNS:
        if pat.search(component):
            return f"internal component ({product} :: {component})"
    if bug.get("type") == "task" and not subjects:
        return "task with no landing subject"
    # Only drop on subject if *every* landing for the bug looks mechanical.
    if subjects:
        reasons = []
        for s in (strip_reviewers(x) for x in subjects):
            hit = next((p.pattern for p in DROP_SUBJECT_PATTERNS if p.search(s)), None)
            if hit is None:
                return None
            reasons.append(hit)
        return f"mechanical subject ({reasons[0]})"
    return None


def census_ids(version: int) -> list[str]:
    """Every bug Bugzilla says landed in `version`, by id.

    Ids only, which is why this needs no paging: one version is a couple of thousand bugs and comes
    back in seconds, while asking for full records in the same query is slow enough to look like it
    does. Full records for the few that matter come from fetch_bugs() afterwards.

    Security-restricted bugs are as invisible here as they are to fetch_bugs, so a census is not a
    check on that blind spot.
    """
    qs = url_parse.urlencode({
        "f1": f"cf_status_firefox{version}",
        "o1": "anyexact",
        "v1": ",".join(LANDED_IN_VERSION),
        "include_fields": "id",
        "order": "bug_id",
        "limit": 0,
    })
    payload = fetch_json(f"{BUGZILLA_REST}?{qs}")
    return [str(b["id"]) for b in payload.get("bugs", [])]


def landings_anywhere(repo: Path, bug_ids: list[str]) -> dict[str, list[str]]:
    """Landing subjects for each bug, across every ref in the clone.

    Attribution is BUG_RE's leading match, the same rule the window path uses: `--grep` also matches
    the message body, so a commit that names the bug only as a dependency, or as what a backout
    blames, is not its landing.

    Bounded by what the clone has -- a beta-only landing is invisible without a beta ref.
    """
    found: dict[str, list[str]] = collections.defaultdict(list)
    for i in range(0, len(bug_ids), 200):
        batch = set(bug_ids[i:i + 200])
        log = git(repo, "log", "--all", "--format=%s", *[f"--grep={b}" for b in sorted(batch)])
        for subject in log.splitlines():
            m = BUG_RE.search(subject)
            # Membership is against this batch, not all ids: a commit matched here for a body mention
            # may lead with a bug from another batch, and would otherwise be recorded twice.
            if m and m.group(1) in batch:
                found[m.group(1)].append(subject)
    return found


def run_census(repo: Path, version: int, window_bugs: set[str], esrs: list[int],
               in_progress: bool = False) -> dict:
    """Bugs flagged as landed in `version` that this window's git enumeration never contained.

    Most of what a census turns up is explained rather than missed, and each explanation is reported
    as its own bucket so the residue is small enough to read. `verified` is the big one: QA sets it on
    the version they tested, so a bug that landed in N-1 and was verified on N is flagged for N
    without ever appearing in N's window -- note_target already knows it belongs to N-1. The rest
    either have no landing of their own, or landed on a ref the window does not cover, which is where
    a beta uplift shows up.
    """
    flagged = census_ids(version)
    unseen = [b for b in flagged if b not in window_bugs]
    print(f"# census: {len(flagged)} bugs flagged fixed in {version}, "
          f"{len(unseen)} outside this window", file=sys.stderr)
    truncated = len(flagged) >= BUGZILLA_MAX_RESULTS
    if truncated:
        print(f"# *** the flagged set hit Bugzilla's {BUGZILLA_MAX_RESULTS}-result cap, so it is "
              "short and these gaps are UNDER-reported", file=sys.stderr)

    bugs, unfetchable = fetch_bugs(unseen, version, esrs) if unseen else ({}, [])
    elsewhere = landings_anywhere(repo, unseen) if unseen else {}
    kept, filtered, earlier, no_landing = [], [], [], []
    for bug_id in unseen:
        bug = bugs.get(bug_id)
        if bug is None:
            continue
        subjects = [s for s in elsewhere.get(bug_id, []) if not REVERT_RE.match(s)]
        rec = {
            "bug": bug_id,
            "summary": bug.get("summary", ""),
            "product": bug.get("product", ""),
            "component": bug.get("component", ""),
            "type": bug.get("type"),
            "keywords": bug.get("keywords", []),
            "status_flag": bug.get(f"cf_status_firefox{version}"),
            "landings": subjects,
            **note_target(bug, version, esrs),
        }
        reason = drop_reason(bug, subjects)
        if reason:
            rec["drop_reason"] = reason
            filtered.append(rec)
        elif rec["note_version"] < version:
            earlier.append(rec)
        elif not subjects:
            no_landing.append(rec)
        else:
            kept.append(rec)
    return {
        "version": version,
        "cycle_in_progress": in_progress,
        "flags": list(LANDED_IN_VERSION),
        "truncated": truncated,
        "flagged": len(flagged),
        "in_window": len(flagged) - len(unseen),
        "outside_window": len(unseen),
        "filtered_mechanical": len(filtered),
        "earlier_version": len(earlier),
        "flag_only": len(no_landing),
        "to_review": len(kept),
        "candidates": kept,
        "earlier": earlier,
        "no_landing": no_landing,
        "filtered": filtered,
        # Searchable but not fetchable should not happen, and dropping it silently would make a short
        # census read as a complete one.
        "unfetchable": sorted(unfetchable),
    }


def window_version(repo: Path, end: str, nightly_now: int) -> int | None:
    """The train `end` belongs to, or None if the boundary tags cannot say.

    A commit belongs to cycle N when it is after FIREFOX_NIGHTLY_{N-1}_END, so the answer is the
    first N walking back whose opening tag `end` does not precede. One `merge-base` call settles the
    common case, where the window is in the current cycle.
    """
    for n in range(nightly_now, nightly_now - 12, -1):
        tag = f"FIREFOX_NIGHTLY_{n - 1}_END"
        if not trainlib.git(repo, "rev-parse", "--verify", "--quiet", tag, check=False).strip():
            return None
        rc, _, _ = trainlib.git_rc(repo, "merge-base", "--is-ancestor", end, tag)
        if rc != 0:
            return n
    return None


def show_state(repo: Path, nightly: int) -> None:
    """Print everything needed to choose a start point, then let the caller ask the user.

    The script stays non-interactive: it reports state, the skill does the asking.
    """
    st = trainlib.watermark_status(repo, trainlib.read_watermark(), nightly)
    print(f"Current Nightly: {nightly}")
    print(f"Watermark file : {trainlib.WATERMARK_FILE}")
    print()
    if not st.get("present"):
        print("No stored watermark -- this looks like a first run.")
    elif not st.get("known"):
        print(f"Stored watermark {st['commit'][:12]} is NOT in this checkout "
              "(different clone, or the commit was never fetched).")
    else:
        print(f"Stored watermark : {st['commit'][:12]}  ({st['date']})")
        print(f"  saved at       : {st.get('saved_at')}"
              + (f"  note: {st['note']}" if st.get("note") else ""))
        print(f"  commits behind {st.get('upstream', 'upstream')}: {st['commits_behind']}")
        if st["stale_train"]:
            print(f"  *** STALE: predates {st['current_cycle_start']}, i.e. an earlier release "
                  "train. Resuming from here would sweep in a whole shipped cycle. "
                  "Pick a newer start below. ***")
        else:
            print("  within the current train -- resuming from here is reasonable.")
    print()
    print("Recent nightly builds (newest first):")
    try:
        builds = trainlib.annotate_builds(trainlib.nightly_builds(limit=10), repo)
    except RuntimeError as e:
        print(f"  (could not load builds: {e})")
        return
    for b in builds:
        mark = "" if b["in_mirror"] else "   [not in local mirror -- fetch needed]"
        git_short = (b["git"] or "?")[:12]
        print(f"  {b['buildid']}  {b.get('app_version',''):<8} git {git_short}  "
              f"{b['git_date']}{mark}")
    print()
    print("Choose one:")
    print("  --since-last                    resume from the watermark")
    print("  --from-build <id>               start from a specific nightly build")
    print("  --build <id>                    exactly one build (previous build -> this one)")
    print(f"  --cycle {nightly}                     the whole current nightly cycle")
    print("Add --save-state to record the new watermark when done.")


def resolve_window(repo: Path, args, nightly_now: int) -> tuple[str, str, str]:
    """Turn the CLI options into (start, end, human-readable basis).

    Deliberately offers no date-based default: commit dates on this repo are
    non-monotonic, so a date boundary silently under-covers. See trainlib's module
    docstring for the measured case (29 of 57 bugs).
    """
    end = args.rev

    if args.to_build:
        b = trainlib.resolve_build(args.to_build)
        if not b or not b.get("git"):
            sys.exit(f"error: could not resolve build {args.to_build} to a git commit")
        end = b["git"]

    if args.rev_range:
        start, end = args.rev_range.split("..", 1)
        return start, end, f"explicit range {args.rev_range}"

    if args.build:
        builds = trainlib.nightly_builds(limit=100000)
        ids = sorted(b["buildid"] for b in builds)
        if args.build not in ids:
            sys.exit(f"error: build {args.build} not found in the nightly build list")
        i = ids.index(args.build)
        if i == 0:
            sys.exit(f"error: {args.build} is the oldest known build; no previous boundary")
        this_b = trainlib.resolve_build(args.build)
        prev_b = trainlib.resolve_build(ids[i - 1])
        if not (this_b and this_b.get("git") and prev_b and prev_b.get("git")):
            sys.exit("error: could not map both build boundaries to git commits")
        return (prev_b["git"], this_b["git"],
                f"nightly build {args.build} (from previous build {ids[i - 1]})")

    if args.build_day:
        bounds = trainlib.day_boundaries(args.build_day)
        if not bounds:
            sys.exit(f"error: no nightly builds found for {args.build_day}")
        prev_id, last_id = bounds
        prev_b = trainlib.resolve_build(prev_id)
        last_b = trainlib.resolve_build(last_id)
        if not (prev_b and prev_b.get("git") and last_b and last_b.get("git")):
            sys.exit("error: could not map the day's build boundaries to git commits")
        n = len(trainlib.builds_on_day(args.build_day))
        return (prev_b["git"], last_b["git"],
                f"all {n} nightly build(s) on {args.build_day} "
                f"(from previous build {prev_id} to {last_id})")

    if args.from_build:
        b = trainlib.resolve_build(args.from_build)
        if not b or not b.get("git"):
            sys.exit(f"error: could not resolve build {args.from_build} to a git commit")
        return b["git"], end, f"from nightly build {args.from_build}"

    if args.cycle:
        rng = trainlib.cycle_range(repo, args.cycle, head=args.rev)
        if not rng:
            sys.exit(f"error: no FIREFOX_NIGHTLY_{args.cycle - 1}_END tag, so the start of "
                     f"cycle {args.cycle} cannot be determined")
        start, cend, in_progress = rng
        note = " IN PROGRESS, scanned to HEAD" if in_progress else ""
        if in_progress:
            print(f"# cycle {args.cycle} has not merged yet; scanning {start}..{args.rev}",
                  file=sys.stderr)
        return start, cend, f"nightly cycle {args.cycle}{note} ({start}..{cend})"

    if args.since_last:
        st = trainlib.watermark_status(repo, trainlib.read_watermark(), nightly_now)
        if not st.get("present"):
            sys.exit("error: no stored watermark. Run --show-state and pick a start point.")
        if not st.get("known"):
            sys.exit(f"error: watermark {st['commit'][:12]} is not in this checkout. "
                     "Run --show-state and pick a start point.")
        if st["stale_train"] and not args.allow_stale:
            sys.exit(
                f"error: watermark {st['commit'][:12]} ({st['date']}) predates "
                f"{st['current_cycle_start']}, so it belongs to an earlier release train and "
                f"would pull in {st['commits_behind']} commits. Pick a newer start "
                "(--from-build / --build / --cycle), or pass --allow-stale to override."
            )
        return st["commit"], end, f"stored watermark {st['commit'][:12]} ({st['date']})"


    sys.exit(
        "error: no window specified. Run --show-state to see where you left off, then use one of "
        "--since-last / --build <id> / --from-build <id> / --cycle N / --range A..B."
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Funnel a firefox-main window to note candidates.")
    p.add_argument("--repo", default=None,
                   help="Gecko checkout (default: saved by watchlist.py check-setup)")
    p.add_argument("--rev", default=trainlib.gecko_upstream(),
                   help="window end, and the head for an in-progress --cycle "
                        "(default: the upstream ref check-setup detected)")
    p.add_argument("--range", dest="rev_range", default=None, help="explicit START..END")
    p.add_argument("--no-fetch", action="store_true")
    p.add_argument("--version", type=int, default=None,
                   help="Firefox version this window belongs to, for the cf_status_firefoxN "
                        "check. Defaults to the current Nightly, which is WRONG for a historical "
                        "window -- pass it explicitly when scanning a past cycle.")
    p.add_argument("--show-state", action="store_true",
                   help="print the stored watermark and recent nightly builds, then exit. Use this "
                        "FIRST on a daily run so the user can choose where to resume from.")
    p.add_argument("--since-last", action="store_true",
                   help="start from the stored watermark (see --show-state)")
    p.add_argument("--from-build", default=None,
                   help="start from a nightly build id, e.g. 20260730214347")
    p.add_argument("--to-build", default=None, help="end at a nightly build id")
    p.add_argument("--build", default=None,
                   help="scan exactly one nightly build: from the previous build to this one")
    p.add_argument("--build-day", default=None,
                   help="every nightly build on YYYYMMDD (previous day's last build -> this "
                        "day's last build). This is the usual review unit.")
    p.add_argument("--cycle", type=int, default=None,
                   help="scan version N's whole nightly cycle via the FIREFOX_NIGHTLY_*_END tags")
    p.add_argument("--census", action="store_true",
                   help="cross-check the window against a Bugzilla-wide search for "
                        "cf_status_firefoxN, and report bugs flagged as landed in N that no commit "
                        "in the window mentions -- beta-only uplifts are the usual find. Requires "
                        "the window to be N's whole cycle.")
    p.add_argument("--allow-stale", action="store_true",
                   help="permit resuming from a watermark that predates the current train")
    p.add_argument("--save-state", action="store_true",
                   help="on success, store the window end as the new watermark")
    p.add_argument("--first-parent", action="store_true",
                   help="follow only the first parent — REQUIRED for a beta-cycle range, where a "
                        "plain two-dot range pulls in every merged main ancestor (71,678 commits "
                        "for 153 instead of 682)")
    p.add_argument("--show-dropped", action="store_true")
    p.add_argument("--census-out", metavar="PATH", default=None,
                   help="also write the census section to PATH, so a --format json run still "
                        "produces the readable version")
    p.add_argument("--dropped-out", metavar="PATH", default=None,
                   help="also write the grouped drop list to PATH. One renderer serves this and "
                        "--show-dropped, so the audit file and the inline listing cannot drift.")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("-o", "--output", default=None)
    args = p.parse_args()

    repo = trainlib.resolve_repo(args.repo)

    if not args.no_fetch:
        trainlib.fetch_origin(repo, remote=trainlib.gecko_remote(),
                              consequence="The window may stop short of the newest landings, so a survivor "
                                    "count from this run can be low without saying so.")

    nightly_now = nightly_version()

    if args.show_state:
        show_state(repo, nightly_now)
        return

    start, end, basis = resolve_window(repo, args, nightly_now)

    start_desc = git(repo, "log", "-1", "--format=%h %cd", "--date=iso", start).strip()
    end_desc = git(repo, "log", "-1", "--format=%h %cd", "--date=iso", end).strip()
    print(f"# window basis: {basis}", file=sys.stderr)
    nightly = args.version or nightly_now
    esrs = esr_versions()
    src = "explicit --version" if args.version else "current Nightly from product-details"
    print(f"# window {start_desc} .. {end_desc} (checking cf_status_firefox{nightly}, {src})",
          file=sys.stderr)

    # Checking a train's status flag against another train's commits mislabels almost every survivor
    # as an uplift, because note_target then resolves `shipped_in` one train too late. One comparison
    # decides it; only the severity varies. A disagreement is fatal when nobody chose the version
    # (the default is the current Nightly, so every historical window needs --version) and when
    # --cycle set the window, where the train is definitional. Any other explicit --version stands:
    # a window straddling merge day genuinely belongs to two trains and the caller picks.
    wv = window_version(repo, end, nightly_now)
    if wv is None:
        if args.version is None:
            print("# WARNING: cannot tell which train this window belongs to -- no boundary tag in "
                  f"this checkout -- so cf_status_firefox{nightly} is unverified", file=sys.stderr)
    elif wv != nightly:
        if args.version is None or args.cycle:
            sys.exit(f"error: this window's commits belong to Firefox {wv}, but the version being "
                     f"checked is {nightly}, so every cf_status_firefox{nightly} lookup is for the "
                     f"wrong train and nearly every survivor comes back as an uplift. "
                     f"Pass --version {wv}.")
        print(f"# NOTE: this window's commits look like Firefox {wv}, not the {args.version} being "
              "checked", file=sys.stderr)

    if args.census:
        if args.first_parent:
            sys.exit("error: --census with --first-parent under-enumerates the window, because the "
                     "landings that arrive through merges are skipped and then reported as gaps. "
                     "Drop --first-parent.")
        rng = trainlib.cycle_range(repo, nightly, head=args.rev)
        if not rng:
            sys.exit(f"error: --census needs cycle bounds for {nightly}, and this checkout has no "
                     f"FIREFOX_NIGHTLY_{nightly - 1}_END tag.")
        c_start, c_end, cycle_open = rng
        resolved = [git(repo, "rev-parse", r).strip() for r in (start, end, c_start, c_end)]
        if resolved[:2] != resolved[2:]:
            sys.exit(
                f"error: --census compares every bug flagged cf_status_firefox{nightly} against "
                f"the bugs in this window, so a window narrower than the cycle reports the rest of "
                f"the cycle as unseen. Window is {start}..{end}; cycle {nightly} is "
                f"{c_start}..{c_end}. Use --cycle {nightly} --version {nightly}."
            )

    # A window spanning merge day straddles two versions: commits before the automatic
    # version bump belong to N-1, those after to N. The uplift heuristic cannot tell
    # "landed in N-1 before the merge" from "uplifted to N-1 afterwards" -- both show an
    # earliest landed version below the Nightly -- so say so rather than mislabel them.
    bump = git(repo, "log", f"{start}..{end}", "--format=%H %cd %s", "--date=short",
               "--", "browser/config/version.txt").strip()
    if bump:
        line = bump.splitlines()[0]
        print(f"# *** WINDOW STRADDLES MERGE DAY: {line[:90]}", file=sys.stderr)
        print("# Commits before that bump belong to the PREVIOUS version, and are not "
              "uplifts even though they are flagged as such.", file=sys.stderr)

    commits = enumerate_commits(repo, start, end, args.first_parent)
    by_bug: dict[str, list[dict]] = collections.defaultdict(list)
    for c in commits:
        if c["bug"]:
            by_bug[c["bug"]].append(c)
    reverted_bugs = {c["bug"] for c in commits if c["is_revert"] and c["bug"]}
    print(f"# {len(commits)} commits, {len(by_bug)} distinct bugs, "
          f"{len(reverted_bugs)} touched by a revert", file=sys.stderr)

    bugs, missing = fetch_bugs(sorted(by_bug), nightly, esrs)

    survivors, dropped = [], []
    # Ids, not just counts. These two buckets used to be numbers with nothing to look at, so a pass
    # auditing coverage had to re-derive them by listing the window's bugs from git and subtracting
    # survivors and dropped -- which is also easy to get wrong, since survivor records key on `bug`
    # rather than `id`. Security-restricted bugs in particular are the window's largest blind spot,
    # and the skill asks for them to be reported, so they have to be nameable.
    not_fixed_ids = []
    for bug_id, landings in sorted(by_bug.items(), key=lambda x: -len(x[1])):
        bug = bugs.get(bug_id)
        if bug is None:
            continue  # security-restricted; listed via `missing`
        if not is_fixed(bug, nightly):
            not_fixed_ids.append({"bug": bug_id, "status": bug.get("status"),
                                  "resolution": bug.get("resolution") or "",
                                  "summary": bug.get("summary", "")})
            continue
        # Judge the bug on its non-revert landings.
        subjects = [c["subject"] for c in landings if not c["is_revert"]]
        reason = drop_reason(bug, subjects)
        rec = {
            "bug": bug_id,
            "summary": bug.get("summary", ""),
            "product": bug.get("product", ""),
            "component": bug.get("component", ""),
            "type": bug.get("type"),
            "keywords": bug.get("keywords", []),
            "whiteboard": bug.get("whiteboard", ""),
            "blocks": bug.get("blocks", []),
            "depends_on": bug.get("depends_on", []),
            "status_flag": bug.get(f"cf_status_firefox{nightly}"),
            "landings": [{"sha": c["sha"], "subject": c["subject"]} for c in landings],
            "commit_count": len(subjects),
            "had_revert": bug_id in reverted_bugs,
            **note_target(bug, nightly, esrs),
        }
        if reason:
            rec["drop_reason"] = reason
            dropped.append(rec)
        else:
            survivors.append(rec)

    census = (run_census(repo, nightly, set(by_bug), esrs, cycle_open)
              if args.census else None)

    # Which revision of the tooling produced this file, carried in the artifact rather than left to
    # the reader's memory of when they ran it. See daily-pass.py's TOOLING header for the same
    # stamp in prose form.
    tooling = trainlib.tooling_stamp()
    result = {
        "tooling": {"version": tooling["version"], "dirty": tooling["dirty"]},
        "window": {
            "start": start, "end": end,
            "start_desc": start_desc, "end_desc": end_desc,
            "nightly_version": nightly,
            "basis": basis,
        },
        "funnel": {
            "commits": len(commits),
            "distinct_bugs": len(by_bug),
            "security_restricted": len(missing),
            "not_currently_fixed": len(not_fixed_ids),
            "dropped_mechanical": len(dropped),
            "survivors": len(survivors),
            "uplifted": sum(1 for s in survivors if s["uplifted"]),
        },
        "survivors": survivors,
        "dropped": dropped,
        # Named apart from the funnel's counts of the same buckets: `funnel.not_currently_fixed` is
        # an int and this is a list, and one name for both invites len() on the number.
        "not_fixed_bugs": not_fixed_ids,
        "security_restricted_bugs": sorted(missing),
        # null when not requested, so a reader can tell "not checked" from "nothing found".
        "census": census,
    }

    if args.format == "json":
        out = json.dumps(result, indent=2)
    else:
        lines = []
        f = result["funnel"]
        lines.append(f"Tooling: {result['tooling']['version']}")
        lines.append(f"Window: {start_desc} .. {end_desc}   (Nightly {nightly})")
        lines.append(
            f"Funnel: {f['commits']} commits -> {f['distinct_bugs']} bugs -> "
            f"{f['distinct_bugs'] - f['not_currently_fixed'] - f['security_restricted']} FIXED -> "
            f"{f['survivors']} survivors "
            f"({f['dropped_mechanical']} dropped as mechanical, "
            f"{f['not_currently_fixed']} not currently FIXED, "
            f"{f['security_restricted']} security-restricted)"
        )
        # Name both, because a count cannot be audited. Security-restricted bugs are the window's
        # largest blind spot and the skill asks for them in the report; a not-currently-FIXED bug is
        # a candidate that may simply land its resolution later.
        if missing:
            lines.append(f"Security-restricted, not fetchable ({len(missing)}): "
                         + capped(sorted(missing)))
        if not_fixed_ids:
            lines.append(f"Not currently FIXED ({len(not_fixed_ids)}): "
                         + capped([f"{r['bug']} [{r['status']}]" for r in not_fixed_ids]))
        if census:
            lines.append("")
            lines += render_census(census)
        lines.append("")
        areas = collections.Counter(f"{s['product']} :: {s['component']}" for s in survivors)
        lines.append(f"Survivors by area ({len(areas)} areas):")
        for area, n in areas.most_common():
            lines.append(f"  {n:>3}  {area}")
        lines.append("")
        lines.append("Survivors (most landings first -- multi-commit bugs are feature-shaped).")
        lines.append("Judge the LANDED lines, not the bug summary: a bug titled like test work")
        lines.append("often lands real changes, and vice versa.")
        lines.append("")
        for s in survivors:
            flags = []
            if s["commit_count"] > 1:
                flags.append(f"{s['commit_count']} commits")
            if s["had_revert"]:
                flags.append("RE-LANDED?")
            if s["uplifted"]:
                flags.append(f"UPLIFTED -> note belongs to {s['note_version']}")
            if s["esr"]:
                flags.append("also ESR " + ", ".join(str(v) for v in s["esr"]))
            if s["status_flag"] == "disabled":
                flags.append("flag=disabled")
            if trainlib.is_meta(s):
                flags.append("meta")
            tag = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {s['bug']}  {s['product']} :: {s['component']}{tag}")
            lines.append(f"        bug: {s['summary'][:140]}")
            shown = [c for c in s["landings"] if not REVERT_RE.match(c["subject"])][:3]
            for c in shown:
                lines.append(f"        landed: {tidy_subject(c['subject'])[:140]}")
            if s["commit_count"] > len(shown):
                lines.append(f"        ... and {s['commit_count'] - len(shown)} more landings")
        if args.show_dropped:
            lines.append("")
            lines += render_dropped(dropped)
        out = "\n".join(lines) + "\n"

    if args.census_out:
        if census is None:
            sys.exit("error: --census-out needs --census; there is nothing to write without it.")
        Path(args.census_out).write_text("\n".join(render_census(census)) + "\n")
        print(f"# wrote {args.census_out} ({census['to_review']} to look at)", file=sys.stderr)

    # Independent of --format: the drop list is the audit artifact, and a JSON run still needs it.
    if args.dropped_out:
        Path(args.dropped_out).write_text("\n".join(render_dropped(dropped)) + "\n")
        print(f"# wrote {args.dropped_out} ({len(dropped)} mechanical drops to audit)",
              file=sys.stderr)

    if args.output:
        Path(args.output).write_text(out)
        print(f"# wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)

    if args.save_state:
        resolved_end = git(repo, "rev-parse", end).strip()
        try:
            path = trainlib.write_watermark(resolved_end, note=basis, repo=repo)
        except RuntimeError as e:
            print(f"# {e}", file=sys.stderr)
            return
        print(f"# watermark saved: {resolved_end[:12]} -> {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
