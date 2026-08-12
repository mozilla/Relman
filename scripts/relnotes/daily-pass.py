#!/usr/bin/env python3
"""Run a whole release-note discovery pass in one invocation.

Replaces the four-or-five separate script calls a pass used to need (scan, pref-delta, bug-tree,
plus ad-hoc Bugzilla lookups). Two reasons this exists:

1. **Fewer approval prompts** -- one command instead of five.
2. **The three analyses cannot drift apart.** They are guaranteed to run over the identical
   revision range, which was easy to get wrong by hand: pref-delta and scan-window took their
   windows from separate flags.

It also adds the two Bugzilla signals that judging a candidate always needs and that no single
script owned before:

- **pending uplift requests** -- `approval-mozilla-*` flags set to `?` on a bug's attachments.
  A pending request means the note's target version is likely to move, and it is a strong hint the
  change is considered worth shipping sooner. (Real case: bug 2050228 had four pending requests --
  beta plus three ESR branches -- which a version-flag check alone does not reveal, because the
  flags still read `fx154=fix-optional`.)
- **the relnote flag** -- `cf_tracking_firefox_relnote`, including the `nightly+` value used to mark
  Nightly-only features worth calling out for testing and feedback.

Read-only. Writes only to the output directory (a mktemp dir unless --outdir is given).

Usage:
  daily-pass.py --build 20260731085738
  daily-pass.py --since-last --save-state
  daily-pass.py --cycle 155
  daily-pass.py --build 20260731085738 --outdir /tmp/pass31
"""

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.parse as url_parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import trainlib  # noqa: E402
import watchlist  # noqa: E402

BUGZILLA_REST = "https://bugzilla.mozilla.org/rest"

def run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    print(f"# $ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, capture_output=capture, text=True, check=False)


def window_args(args) -> list[str]:
    out = []
    if args.build:
        out += ["--build", args.build]
    if args.from_build:
        out += ["--from-build", args.from_build]
    if args.to_build:
        out += ["--to-build", args.to_build]
    if args.build_day:
        out += ["--build-day", args.build_day]
    if args.cycle:
        out += ["--cycle", str(args.cycle)]
    if args.since_last:
        out += ["--since-last"]
    if args.rev_range:
        out += ["--range", args.rev_range]
    if args.version:
        out += ["--version", str(args.version)]
    if args.first_parent:
        out += ["--first-parent"]
    if args.allow_stale:
        out += ["--allow-stale"]
    out += ["--repo", str(trainlib.resolve_repo(args.repo))]
    if args.no_fetch:
        out += ["--no-fetch"]
    return out


def buglist_url(bug_ids) -> str:
    """A Bugzilla buglist link for a set of bugs, so they can all be opened at once.

    Uses bug_id / anyexact, which is what Bugzilla's own "show these bugs" links use and
    what whattrainisitnow encodes in its "Patches from N bugs" link.
    """
    ids = ",".join(sorted(set(str(b) for b in bug_ids), key=lambda x: int(x)))
    return ("https://bugzilla.mozilla.org/buglist.cgi?bug_id_type=anyexact&bug_id=" + ids)


def fetch_uplift_and_relnote_flags(bug_ids: list[str]) -> dict[str, dict]:
    """Pending uplift requests and the relnote flag, per bug.

    Uplift requests live on *attachment* flags, not on the bug, so they need a separate
    endpoint from the main bug fetch.
    """
    out: dict[str, dict] = {b: {"pending_uplift": [], "relnote_flag": None} for b in bug_ids}
    if not bug_ids:
        return out

    for i in range(0, len(bug_ids), 120):
        batch = bug_ids[i:i + 120]
        qs = url_parse.urlencode({
            "id": ",".join(batch),
            "include_fields": ("id,cf_tracking_firefox_relnote,creator,duplicates,cc_count,"
                               "see_also,groups,creation_time"),
        })
        try:
            payload = trainlib.fetch_json(f"{BUGZILLA_REST}/bug?{qs}")
        except RuntimeError as e:
            print(f"# warning: relnote-flag fetch failed: {e}", file=sys.stderr)
            break
        for b in payload.get("bugs", []):
            bid = str(b["id"])
            flag = b.get("cf_tracking_firefox_relnote")
            if flag and flag not in ("---", ""):
                out[bid]["relnote_flag"] = flag
            # Impact evidence. Repeatedly the deciding factor: a bug filed internally
            # with no duplicates is weak evidence of user impact no matter how severe
            # the summary sounds, while duplicates and outside reporters are real
            # evidence people hit it.
            creator = b.get("creator") or ""
            out[bid]["reporter"] = creator
            out[bid]["internal"] = creator.endswith("@mozilla.com")
            out[bid]["duplicates"] = len(b.get("duplicates") or [])
            out[bid]["see_also"] = len(b.get("see_also") or [])
            out[bid]["public"] = not (b.get("groups") or [])

    # Uplift approval lives on *attachment* flags, and the attachment endpoint takes one
    # bug at a time -- which was 127 requests on a busy day. Narrow first with a single
    # search on flagtypes.name, then fetch attachments only for the few bugs that have an
    # approval flag at all.
    candidates: list[str] = []
    for i in range(0, len(bug_ids), 120):
        qs = url_parse.urlencode({
            "id": ",".join(bug_ids[i:i + 120]), "include_fields": "id",
            "f1": "flagtypes.name", "o1": "substring", "v1": "approval-mozilla",
        })
        try:
            hit = trainlib.fetch_json(f"{BUGZILLA_REST}/bug?{qs}")
        except RuntimeError as e:
            # Do not let a failed narrowing pass masquerade as "nothing pending" --
            # pending-uplift is used to *reject* candidates, so a silent miss changes
            # the verdict.
            print(f"# WARNING: uplift-flag search failed ({e}); pending-uplift results "
                  "for this run are INCOMPLETE", file=sys.stderr)
            candidates = list(bug_ids)
            break
        candidates += [str(b["id"]) for b in hit.get("bugs", [])]

    for bug in candidates:
        try:
            payload = trainlib.fetch_json(
                f"{BUGZILLA_REST}/bug/{bug}/attachment?exclude_fields=data"
            )
        except RuntimeError as e:
            print(f"# WARNING: could not read attachments for bug {bug} ({e}); its "
                  "pending-uplift state is unknown", file=sys.stderr)
            continue
        for atts in (payload.get("bugs") or {}).values():
            for a in atts:
                for fl in a.get("flags", []):
                    name = fl.get("name", "")
                    if name.startswith("approval-mozilla-") and fl.get("status") == "?":
                        target = name.replace("approval-mozilla-", "")
                        if target not in out[bug]["pending_uplift"]:
                            out[bug]["pending_uplift"].append(target)
    return out


class Tee:
    """Collect everything printed so the full report can be written to disk.

    daily-pass output runs to tens of KB, which invites `> file` redirects -- and those
    are file writes that prompt for approval separately from the script. Writing the
    report ourselves removes the reason to redirect.
    """

    def __init__(self, stream, echo=True):
        self.stream = stream
        self.echo = echo
        self.buf = []

    def write(self, text):
        self.buf.append(text)
        if self.echo:
            self.stream.write(text)

    def flush(self):
        self.stream.flush()

    def text(self):
        return "".join(self.buf)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Run scan-window, pref-delta and bug-tree over one window, in one call.",
    )
    # Window selection (forwarded to scan-window.py; same semantics).
    p.add_argument("--build", default=None)
    p.add_argument("--from-build", default=None)
    p.add_argument("--to-build", default=None)
    p.add_argument("--build-day", default=None)
    p.add_argument("--cycle", type=int, default=None)
    p.add_argument("--since-last", action="store_true")
    p.add_argument("--range", dest="rev_range", default=None)
    p.add_argument("--version", type=int, default=None)
    p.add_argument("--first-parent", action="store_true")
    p.add_argument("--allow-stale", action="store_true")
    p.add_argument("--save-state", action="store_true")
    p.add_argument("--repo", default=None)
    p.add_argument("--no-fetch", action="store_true")
    # Pass options.
    p.add_argument("--outdir", default=None, help="default: a fresh mktemp dir")
    p.add_argument("--min-cluster", type=int, default=2)
    p.add_argument("--show-dropped", action="store_true")
    p.add_argument("--brief", action="store_true",
                   help="print only the funnel and headline signals; the full report is always "
                        "written to <outdir>/report.txt. Use this instead of redirecting output -- "
                        "a shell redirect is a file write and needs its own approval.")
    p.add_argument("--skip-flags", action="store_true",
                   help="skip the per-bug uplift/relnote flag pass (one request per survivor)")
    args = p.parse_args()

    if not any([args.build, args.build_day, args.from_build, args.cycle, args.since_last,
                args.rev_range]):
        sys.exit(
            "error: no window specified. Run `scan-window.py --show-state` first, then pass one of "
            "--since-last / --build <id> / --from-build <id> / --cycle N / --range A..B."
        )

    outdir = Path(args.outdir) if args.outdir else Path(tempfile.mkdtemp(prefix="relnotes-pass-"))
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"# output dir: {outdir}", file=sys.stderr)

    scan_json = outdir / "scan.json"
    wargs = window_args(args)

    # --- 1. scan -------------------------------------------------------------
    r = run([sys.executable, str(HERE / "scan-window.py"), *wargs,
             "--format", "json", "-o", str(scan_json)])
    sys.stderr.write(r.stderr)
    if r.returncode != 0 or not scan_json.exists():
        sys.exit("error: scan-window.py failed; see above")
    scan = json.loads(scan_json.read_text())
    window = scan["window"]
    rng = f"{window['start']}..{window['end']}"

    # Human-readable survivor list, same window. The drop list comes out of the same run via
    # --dropped-out: the audit needs it every pass, and asking the owner of the data to render it
    # keeps one shape rather than a second copy of the formatting here.
    r2 = run([sys.executable, str(HERE / "scan-window.py"), *wargs,
              "--dropped-out", str(outdir / "dropped.txt"),
              *(["--show-dropped"] if args.show_dropped else [])])
    (outdir / "scan.txt").write_text(r2.stdout)

    # The drop list as its own complete file, unconditionally: the audit is a required step every
    # pass, and giving it a file removes the reason to rebuild the list through a pipe that can
    # truncate it. Built from the scan JSON already in hand, so it costs nothing.
    # scan-window renders and writes it, via --dropped-out on the run above: one renderer, so the
    # audit file and `--show-dropped` cannot drift into two shapes for the same data. The count is
    # still read from the scan JSON here, for the footer and the reconciliation warning below.
    dropped = scan.get("dropped", [])
    # Check the count in the file, not just that a file is there. scan-window writes it only on a
    # successful run, and the survivor pass does its own Bugzilla fetch, so it can fail while the
    # JSON pass succeeded -- leaving a previous run's dropped.txt in a reused outdir to satisfy an
    # existence test. The header carries the count, so one comparison catches both cases.
    drop_file = outdir / "dropped.txt"
    header = drop_file.read_text().splitlines()[0] if drop_file.exists() else ""
    if f"({len(dropped)})" not in header:
        print(f"# WARNING: {drop_file} is missing or stale -- its header reads {header!r} while this "
              f"window has {len(dropped)} mechanical drops. The drop-list audit has no valid input.",
              file=sys.stderr)
    # The header count and the funnel come from different keys of the same scan, so they agree
    # unless scan-window's JSON shape changes. Say so rather than leaving the reader to notice:
    # a silent disagreement here reads as "nothing left to audit", which is the one conclusion
    # this file exists to stop anyone drawing without evidence.
    drop_mismatch = ""
    if len(dropped) != scan["funnel"]["dropped_mechanical"]:
        drop_mismatch = (f"dropped.txt lists {len(dropped)} entries but the funnel counts "
                         f"{scan['funnel']['dropped_mechanical']} mechanical drops; the drop list "
                         "is incomplete and the audit cannot be called done")
        print(f"# WARNING: {drop_mismatch}", file=sys.stderr)

    # --- 2. preference flips over the identical range ------------------------
    r3 = run([sys.executable, str(HERE / "pref-delta.py"), "--range", rng, "--no-fetch",
              "--repo", str(trainlib.resolve_repo(args.repo))])
    (outdir / "prefs.txt").write_text(r3.stdout)

    # --- 3. feature clusters -------------------------------------------------
    r4 = run([sys.executable, str(HERE / "bug-tree.py"), "--input", str(scan_json),
              "--min-cluster", str(args.min_cluster),
              "--repo", str(trainlib.resolve_repo(args.repo))])
    (outdir / "clusters.txt").write_text(r4.stdout)

    # Only the first scan's exit status was ever checked, and the other three children feed sections
    # whose empty form is an assertion: an exited pref-delta printed "No preference changes." with its
    # error text dropped, which is the one sentence meaning "checked, nothing live". Collect the
    # failures so both channels can say so -- stderr for whoever is watching, and the report body
    # because report.txt is what gets re-read afterwards.
    failures = []
    for label, res, artifact in (("scan-window (survivor list)", r2, "scan.txt"),
                                 ("pref-delta", r3, "prefs.txt"),
                                 ("bug-tree", r4, "clusters.txt")):
        if res.returncode != 0:
            sys.stderr.write(res.stderr)
            failures.append((label, res.returncode, artifact))
            print(f"# WARNING: {label} exited {res.returncode}; {artifact} is incomplete and the "
                  "matching report section is NOT evidence of absence", file=sys.stderr)

    # --- 4. uplift + relnote flags for the survivors -------------------------
    survivors = [s["bug"] for s in scan["survivors"]]
    flags = {}
    if not args.skip_flags:
        print(f"# fetching uplift/relnote flags for {len(survivors)} survivors...",
              file=sys.stderr)
        flags = fetch_uplift_and_relnote_flags(survivors)
        (outdir / "flags.json").write_text(json.dumps(flags, indent=2))

    # --- report --------------------------------------------------------------
    real_stdout = sys.stdout
    tee = Tee(real_stdout, echo=not args.brief)
    sys.stdout = tee

    f = scan["funnel"]
    print()
    print(f"WINDOW  {window['start_desc']} .. {window['end_desc']}")
    print(f"BASIS   {window.get('basis', 'see scan.txt')}")
    print(f"FUNNEL  {f['commits']} commits -> {f['distinct_bugs']} bugs -> {f['survivors']} "
          f"survivors  ({f['dropped_mechanical']} mechanical, {f['not_currently_fixed']} not "
          f"FIXED, {f['security_restricted']} security-restricted, {f.get('uplifted', 0)} uplifted)")
    print()

    if failures:
        print("INCOMPLETE STEPS -- the matching sections below are not evidence of absence:")
        for label, code, artifact in failures:
            print(f"  {label} exited {code}; {artifact} is incomplete")
        print()

    if r3.returncode != 0:
        print(f"PREFERENCE DELTA UNAVAILABLE -- pref-delta exited {r3.returncode}. This is not "
              "'no preference changes'; the window was never checked for flips.")
    else:
        print(r3.stdout.rstrip() or "No preference changes.")
    print()

    if flags:
        pending = {b: v["pending_uplift"] for b, v in flags.items() if v["pending_uplift"]}
        noted = {b: v["relnote_flag"] for b, v in flags.items() if v["relnote_flag"]}
        print("PENDING UPLIFT REQUESTS (target version may move; also a signal the change "
              "is wanted sooner):")
        if pending:
            for b, targets in sorted(pending.items()):
                print(f"  bug {b}: approval requested for {', '.join(sorted(targets))}")
        else:
            print("  none")
        print()
        weak, strong = [], []
        for b, v in flags.items():
            if "internal" not in v:
                continue
            dupes = v.get("duplicates", 0)
            if v["internal"] and dupes == 0:
                weak.append((b, v))
            elif dupes or not v["internal"]:
                strong.append((b, v))
        print("IMPACT EVIDENCE -- outside reporter and/or duplicates (people actually hit it):")
        for b, v in sorted(strong, key=lambda kv: -kv[1].get("duplicates", 0))[:15]:
            who = "internal" if v["internal"] else f"EXTERNAL ({v['reporter'].split('@')[-1]})"
            print(f"  bug {b}: {who}, {v.get('duplicates',0)} dupes, "
                  f"{v.get('see_also',0)} see_also")
        if not strong:
            print("  none")
        print()
        print(f"WEAK IMPACT EVIDENCE -- filed internally, no duplicates ({len(weak)} bugs): "
              "treat a severe-sounding summary here with suspicion")
        print("  " + ", ".join(b for b, _ in sorted(weak)) if weak else "  none")
        print()

        print("RELNOTE FLAG ALREADY SET (someone has already made a call here):")
        if noted:
            for b, fl in sorted(noted.items()):
                print(f"  bug {b}: {fl}")
        else:
            print("  none")
        print()

    tracked = watchlist.annotate(survivors)
    if tracked:
        print("ALREADY ON YOUR WATCHLIST (don't re-ask):")
        for b, it in sorted(tracked.items()):
            print(f"  bug {b}: [{it.get('status','?')}] Fx{it.get('release','?')} "
                  f"{it.get('summary','')}")
        print()
    standing = watchlist.standing(exclude=set(tracked))
    if standing:
        print("STANDING WATCHLIST (not in this window; re-surface when the gate flips):")
        for k, it in sorted(standing.items()):
            print(f"  {k}: [{it.get('status','?')}] Fx{it.get('release','?')} "
                  f"{it.get('summary','')}")
        print()

    if r4.returncode != 0:
        print(f"FEATURE CLUSTERS UNAVAILABLE -- bug-tree exited {r4.returncode}. This is not "
              "'no feature clusters'; the survivors were never clustered.")
    else:
        print(r4.stdout.rstrip() or "No feature clusters.")
    print()
    print("BUGZILLA LINKS (open them all at once):")
    if survivors:
        print(f"  survivors ({len(survivors)}):")
        print(f"    {buglist_url(survivors)}")
    dropped_ids = [d["bug"] for d in scan.get("dropped", [])]
    if dropped_ids:
        print(f"  mechanically dropped ({len(dropped_ids)}), for auditing:")
        print(f"    {buglist_url(dropped_ids)}")
    print()

    print(f"Full output written to {outdir}/ (scan.json, scan.txt, dropped.txt, prefs.txt, "
          "clusters.txt" + (", flags.json" if flags else "") + ")")
    print(f"Survivor list with landings: {outdir}/scan.txt")
    print(f"Drop list to audit ({len(dropped)} entries): {outdir}/dropped.txt")
    # Also in the report body, because report.txt is what gets re-read later and stderr is not
    # captured into it.
    if drop_mismatch:
        print(f"WARNING: {drop_mismatch}")

    sys.stdout = real_stdout
    (outdir / "report.txt").write_text(tee.text())
    if args.brief:
        fu = scan["funnel"]
        print(f"WINDOW  {window['start_desc']} .. {window['end_desc']}")
        print(f"BASIS   {window.get('basis', '')}")
        print(f"FUNNEL  {fu['commits']} commits -> {fu['distinct_bugs']} bugs -> "
              f"{fu['survivors']} survivors  ({fu['dropped_mechanical']} mechanical, "
              f"{fu['not_currently_fixed']} not FIXED, {fu['security_restricted']} "
              f"security-restricted, {fu.get('uplifted', 0)} uplifted)")
        prefs = (outdir / "prefs.txt").read_text()
        flips = [l for l in prefs.splitlines() if l.startswith("== ")]
        print("PREFS   " + ("; ".join(flips) if flips else "no preference changes"))
        print(f"\nFull report: {outdir}/report.txt")
        print(f"Survivors:   {outdir}/scan.txt")
        print(f"Drops:       {outdir}/dropped.txt  ({len(dropped)} to audit)")
    else:
        print(f"\n(full report also written to {outdir}/report.txt)")

    if args.save_state:
        # Write the watermark directly. Re-invoking scan-window.py with --save-state would
        # repeat the whole enumeration and Bugzilla fetch to record one line of state --
        # minutes of duplicated work and hundreds of duplicate requests on a large window.
        # The window end is already known from the scan we just did.
        try:
            repo_path = trainlib.resolve_repo(args.repo)
            resolved_end = trainlib.git(repo_path, "rev-parse",
                                        window["end"]).strip()
            path = trainlib.write_watermark(resolved_end, note=window.get("basis", ""),
                                            repo=repo_path)
            print(f"# watermark saved: {resolved_end[:12]} -> {path}", file=sys.stderr)
        except RuntimeError as e:
            print(f"# WARNING: could not save the watermark ({e}); the next --since-last run "
                  "will resume from the OLD position and re-report this window",
                  file=sys.stderr)


if __name__ == "__main__":
    main()
