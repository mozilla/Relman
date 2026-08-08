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
import subprocess
import sys
import urllib.parse as url_parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

BUGZILLA_REST = "https://bugzilla.mozilla.org/rest/bug"
PRODUCT_DETAILS = "https://product-details.mozilla.org/1.0/firefox_versions.json"
USER_AGENT = "Relman-relnotes-scan/1.0"

BUG_RE = re.compile(r"\b[Bb]ug\s+(\d{5,8})\b")
REVERT_RE = re.compile(r"^\s*(Revert\b|Backed out\b|backout\b)", re.IGNORECASE)

# Products/components that reliably produce nothing. Grounded in the survey's
# zero-yield analysis rather than intuition.
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
        r"(rename|move|extract|inline|deduplicate|refactor)\w*\b",
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
    if flag in ("fixed", "verified", "disabled"):
        return True
    if flag in ("wontfix", "disabled-by-default", "unaffected"):
        return False
    # Flag unset (older bug, or the flag simply isn't used) -- fall back to resolution.
    return bug.get("resolution") == "FIXED" and bug.get("status") in ("RESOLVED", "VERIFIED")


LANDED_FLAGS = ("fixed", "verified")


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
        if bug.get(f"cf_status_firefox{v}") in LANDED_FLAGS:
            shipped_in.append(v)
    esr_hits = [v for v in esrs if bug.get(f"cf_status_firefox_esr{v}") in LANDED_FLAGS]
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
        print(f"  commits behind origin/main: {st['commits_behind']}")
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
    p.add_argument("--rev", default="origin/main")
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
    p.add_argument("--allow-stale", action="store_true",
                   help="permit resuming from a watermark that predates the current train")
    p.add_argument("--save-state", action="store_true",
                   help="on success, store the window end as the new watermark")
    p.add_argument("--first-parent", action="store_true",
                   help="follow only the first parent — REQUIRED for a beta-cycle range, where a "
                        "plain two-dot range pulls in every merged main ancestor (71,678 commits "
                        "for 153 instead of 682)")
    p.add_argument("--show-dropped", action="store_true")
    p.add_argument("--format", choices=["text", "json"], default="text")
    p.add_argument("-o", "--output", default=None)
    args = p.parse_args()

    repo = trainlib.resolve_repo(args.repo)

    if not args.no_fetch:
        print("# fetching origin...", file=sys.stderr)
        subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "origin"], check=False)

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
    not_fixed = 0
    for bug_id, landings in sorted(by_bug.items(), key=lambda x: -len(x[1])):
        bug = bugs.get(bug_id)
        if bug is None:
            continue  # security-restricted; counted separately
        if not is_fixed(bug, nightly):
            not_fixed += 1
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

    result = {
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
            "not_currently_fixed": not_fixed,
            "dropped_mechanical": len(dropped),
            "survivors": len(survivors),
            "uplifted": sum(1 for s in survivors if s["uplifted"]),
        },
        "survivors": survivors,
        "dropped": dropped,
    }

    if args.format == "json":
        out = json.dumps(result, indent=2)
    else:
        lines = []
        f = result["funnel"]
        lines.append(f"Window: {start_desc} .. {end_desc}   (Nightly {nightly})")
        lines.append(
            f"Funnel: {f['commits']} commits -> {f['distinct_bugs']} bugs -> "
            f"{f['distinct_bugs'] - f['not_currently_fixed'] - f['security_restricted']} FIXED -> "
            f"{f['survivors']} survivors "
            f"({f['dropped_mechanical']} dropped as mechanical, "
            f"{f['not_currently_fixed']} not currently FIXED, "
            f"{f['security_restricted']} security-restricted)"
        )
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
            if "meta" in s["keywords"]:
                flags.append("meta")
            tag = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {s['bug']}  {s['product']} :: {s['component']}{tag}")
            lines.append(f"        bug: {s['summary'][:140]}")
            shown = [c for c in s["landings"] if not REVERT_RE.match(c["subject"])][:3]
            for c in shown:
                # Strip the redundant "Bug NNNN - " prefix and the reviewer suffix.
                # Handles "Bug N - ", "Bug N: ", and the "Bug N p2 - " part convention.
                subj = re.sub(
                    r"^\s*[Bb]ug\s+\d+\s*(?:[pP](?:art)?\s*\d+)?\s*[-:.]\s*", "", c["subject"]
                )
                subj = re.sub(r"\s+r[=?][\w#,.\- ]+$", "", subj)
                lines.append(f"        landed: {subj[:140]}")
            if s["commit_count"] > len(shown):
                lines.append(f"        ... and {s['commit_count'] - len(shown)} more landings")
        if args.show_dropped:
            lines.append("")
            # The count is in the header so a truncated read of this list is self-evident.
            lines.append(f"Dropped as mechanical ({len(dropped)}):")
            for d in dropped:
                lines.append(f"  {d['bug']}  {d['summary'][:90]}")
                lines.append(f"        -> {d['drop_reason']}")
        out = "\n".join(lines) + "\n"

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
