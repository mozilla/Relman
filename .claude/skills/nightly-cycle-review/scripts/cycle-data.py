#!/usr/bin/env python3
"""Collect the raw data for a Nightly cycle review.

Enumerates every commit in version N's Nightly cycle, groups them by bug, measures their size,
fetches the bugs and the regressions filed against them from Bugzilla, and writes:

  <outdir>/cycle.json   every bug and commit with all collected fields
  <outdir>/summary.md   the aggregates and ranked lists the review is built from

Usage:
  cycle-data.py --version 158
  cycle-data.py --version 158 --repo /path/to/firefox --outdir /tmp/cycle-158
  cycle-data.py --version 159 --range FIREFOX_NIGHTLY_158_END..origin/main   # cycle in progress

Security-restricted bugs need a Bugzilla API key, read from BUGZILLA_API_KEY or else from
~/.config/bugzilla/api-key. Without one they are listed as restricted.
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup_check  # noqa: E402

BUGZILLA_REST = "https://bugzilla.mozilla.org/rest"
USER_AGENT = "relman-nightly-cycle-review"
BATCH = 120
KEY = None

BUG_RE = re.compile(r"\b[Bb]ug\s+(\d{5,8})\b")
REVERT_RE = re.compile(r"^\s*([Bb]ug\s+\d+\s*[:,-]?\s*)?(Revert\b|Backed out\b|Backout\b)",
                       re.IGNORECASE)
TEST_PATH_RE = re.compile(
    r"(^testing/|/tests?/|/mochitests?/|/crashtests?/|/reftests?/|/gtest/|/jsapi-tests/"
    r"|/androidTest/|/test/|(^|/)test_[^/]*$|(^|/)browser_[^/]*\.js$|\.snap$)"
)
VENDORED_PREFIXES = (
    "third_party/", "other-licenses/", "intl/icu/source/", "gfx/skia/skia/", "gfx/angle/checkout/",
    "gfx/harfbuzz/", "gfx/cairo/", "media/libaom/", "media/libdav1d/", "media/libvpx/",
    "media/ffvpx/", "security/nss/", "nsprpub/", "modules/zlib/src/", "modules/freetype2/", "parser/expat/lib/",
    "extensions/spellcheck/hunspell/src/",
    "js/src/irregexp/imported/", "testing/web-platform/tests/",
)
VENDORED_RE = re.compile(r"(^media/lib[^/]+/|/generated/|(^|/)Generated[^/]*$)")
GENERATED_NAMES = ("Cargo.lock", "supply-chain/audits.toml", "supply-chain/imports.lock")
DEEP_AREAS = ("browser/components/", "toolkit/components/", "mobile/android/", "dom/media/",
              "js/src/", "netwerk/protocol/", "gfx/layers/", "browser/extensions/")
TRAIN_HOP_PREFIX = "browser/extensions/newtab/"
TRAIN_HOP_PATHS = (TRAIN_HOP_PREFIX, "browser/locales/en-US/browser/newtab/")
NOT_PRODUCT_RE = re.compile(r"(^tools/@types/|^[^/]+\.config\.(js|mjs)$|^\.[^/]+$)")
SEVERITY_RANK = {"S1": 1, "S2": 2, "S3": 3, "S4": 4}
OPEN_STATUSES = ("UNCONFIRMED", "NEW", "ASSIGNED", "REOPENED")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, errors="replace").stdout


def api_key():
    return KEY


def fetch_json(url):
    headers = {"User-Agent": USER_AGENT}
    if api_key():
        headers["X-BUGZILLA-API-KEY"] = api_key()
    last = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = RuntimeError(f"HTTP {e.code} {e.reason} for {url[:200]}")
            if e.code < 500 and e.code != 429:
                raise last from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = RuntimeError(f"could not reach Bugzilla: {getattr(e, 'reason', e)}")
        if attempt < 3:
            time.sleep(2 ** attempt)
    raise last


def fetch_bugs(ids, fields, label):
    ids = sorted({str(i) for i in ids}, key=int)
    out = {}
    for i in range(0, len(ids), BATCH):
        qs = urllib.parse.urlencode({"id": ",".join(ids[i:i + BATCH]), "include_fields": fields})
        for bug in fetch_json(f"{BUGZILLA_REST}/bug?{qs}").get("bugs", []):
            out[str(bug["id"])] = bug
        print(f"# fetched {min(i + BATCH, len(ids))}/{len(ids)} {label}", file=sys.stderr)
    return out


def classify_path(path):
    if (path.endswith(GENERATED_NAMES) or path.startswith(VENDORED_PREFIXES)
            or VENDORED_RE.search(path)):
        return "vendored"
    if TEST_PATH_RE.search(path):
        return "test"
    return "code"


def area_of(path):
    parts = path.split("/")
    depth = 3 if path.startswith(DEEP_AREAS) else 2
    return "/".join(parts[:depth]) if len(parts) > depth else "/".join(parts[:-1]) or "(root)"


def read_commits(repo, rev_range):
    fmt = "\x1e%H\x1f%an\x1f%cI\x1f%s"
    raw = git(repo, "log", "--no-merges", "--numstat", f"--format={fmt}", rev_range)
    commits = []
    for chunk in raw.split("\x1e")[1:]:
        header, _, body = chunk.partition("\n")
        sha, author, date, subject = header.split("\x1f", 3)
        lines = {"code": [0, 0], "test": [0, 0], "vendored": [0, 0]}
        areas = collections.Counter()
        files = 0
        paths = []
        for row in body.strip().splitlines():
            added, deleted, path = row.split("\t", 2)
            if "=>" in path:
                path = re.sub(r"\{[^{}]*=> ([^{}]*)\}", r"\1", path).split(" => ")[-1]
            a = int(added) if added.isdigit() else 0
            d = int(deleted) if deleted.isdigit() else 0
            kind = classify_path(path)
            lines[kind][0] += a
            lines[kind][1] += d
            files += 1
            paths.append(path)
            if kind == "code":
                areas[area_of(path)] += a + d
        m = BUG_RE.search(subject)
        commits.append({
            "sha": sha[:12], "author": author, "date": date, "subject": subject,
            "bug": m.group(1) if m else None, "revert": bool(REVERT_RE.match(subject)),
            "files": files, "lines": lines, "areas": dict(areas), "paths": paths,
        })
    return commits


def blob_ids(repo, specs):
    out = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname)"],
                         input="\n".join(specs) + "\n", capture_output=True, text=True).stdout
    return [None if line.endswith("missing") else line for line in out.splitlines()]


def patch_state(repo, end, sha, paths):
    """Apply `sha`'s patch to `end`'s copies of its files: "yes" if it is there, "no" if it is not."""
    patch = subprocess.run(["git", "-C", str(repo), "show", "--format=", sha],
                           capture_output=True).stdout
    with tempfile.TemporaryDirectory() as tmp:
        for path in paths:
            blob = subprocess.run(["git", "-C", str(repo), "show", f"{end}:{path}"],
                                  capture_output=True)
            if blob.returncode == 0:
                target = Path(tmp, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob.stdout)
        applies = lambda *a: subprocess.run(["git", "apply", "--check", *a, "-"], input=patch,
                                            cwd=tmp, capture_output=True).returncode == 0
        present, absent = applies("-R"), applies()
    if present != absent:
        return "yes" if present else "no"
    return None


def last_landing_state(repo, end, commit):
    """For a bug whose newest commit is a revert: is its newest landing still in `end`?"""
    paths = commit["paths"]
    if not paths:
        return "yes"
    state = patch_state(repo, end, commit["sha"], paths)
    if state:
        return state
    sha = commit["sha"]
    ends = blob_ids(repo, [f"{end}:{p}" for p in paths])
    after = blob_ids(repo, [f"{sha}:{p}" for p in paths])
    before = blob_ids(repo, [f"{sha}^:{p}" for p in paths])
    reverted = sum(e == b != a for e, a, b in zip(ends, after, before))
    if reverted == len(paths):
        return "no"
    return "mostly-not" if reverted * 2 >= len(paths) else "yes"


def worst_severity(bugs):
    ranks = [SEVERITY_RANK[b["severity"]] for b in bugs if b.get("severity") in SEVERITY_RANK]
    return f"S{min(ranks)}" if ranks else "--"


def build(args):
    repo = Path(args.repo).expanduser()
    n = args.version
    rev_range = args.range or f"FIREFOX_NIGHTLY_{n - 1}_END..FIREFOX_NIGHTLY_{n}_END"
    start, end = rev_range.split("..")
    start_date = datetime.fromisoformat(git(repo, "log", "-1", "--format=%cI", start).strip())
    end_date = datetime.fromisoformat(git(repo, "log", "-1", "--format=%cI", end).strip())

    commits = read_commits(repo, rev_range)
    changed_in_cycle = set(git(repo, "diff", "--name-only", "--no-renames", start, end).splitlines())
    by_bug = collections.defaultdict(list)
    for c in commits:
        if c["bug"]:
            by_bug[c["bug"]].append(c)

    status_f, tracking_f = f"cf_status_firefox{n}", f"cf_tracking_firefox{n}"
    bug_fields = ",".join(["id", "summary", "product", "component", "type", "severity", "priority",
                           "status", "resolution", "keywords", "whiteboard", "regressed_by",
                           "regressions", status_f, tracking_f])
    bugs = fetch_bugs(by_bug, bug_fields, "cycle bugs")
    restricted = sorted(set(by_bug) - set(bugs), key=int)

    regression_ids = {str(r) for b in bugs.values() for r in b.get("regressions", [])}
    reg_fields = ",".join(["id", "summary", "product", "component", "type", "severity", "status",
                           "resolution", "keywords", "creation_time", status_f, tracking_f])
    regressions = fetch_bugs(regression_ids, reg_fields, "regression bugs") if regression_ids else {}

    records = []
    for bug_id, cs in by_bug.items():
        landings = [c for c in cs if not c["revert"]]
        reverts = [c for c in cs if c["revert"]]
        lines = {k: sum(c["lines"][k][0] + c["lines"][k][1] for c in landings)
                 for k in ("code", "test", "vendored")}
        areas = collections.Counter()
        for c in landings:
            areas.update(c["areas"])
        last = max((datetime.fromisoformat(c["date"]) for c in landings), default=None)
        touched = {p for c in landings for p in c["paths"]}
        product = [p for p in touched
                   if classify_path(p) != "test" and not NOT_PRODUCT_RE.search(p)]
        riding = sorted(p for p in product if not p.startswith(TRAIN_HOP_PATHS))
        if not product or len(riding) == len(product):
            train_hop = ""
        else:
            train_hop = "partly" if riding else "yes"
        net = len(touched & changed_in_cycle)
        if not touched:
            in_tree = "no-files"
        elif net == 0:
            in_tree = "no"
        elif net / len(touched) < 0.5:
            in_tree = "mostly-not"
        else:
            in_tree = "yes"
        if in_tree == "yes" and cs[0]["revert"] and landings:
            in_tree = last_landing_state(repo, end, landings[0])
        b = bugs.get(bug_id)
        regs = []
        for r in (b or {}).get("regressions", []):
            rb = regressions.get(str(r))
            if rb is None:
                regs.append({"id": r, "restricted": True})
                continue
            regs.append({"id": r, "summary": rb["summary"], "product": rb["product"],
                         "component": rb["component"], "severity": rb.get("severity"),
                         "status": rb["status"], "resolution": rb.get("resolution"),
                         "open": rb["status"] in OPEN_STATUSES, "created": rb["creation_time"],
                         "before_cycle": datetime.fromisoformat(
                             rb["creation_time"].replace("Z", "+00:00")) < start_date,
                         "status_n": rb.get(status_f), "tracking_n": rb.get(tracking_f)})
        records.append({
            "id": bug_id, "restricted": b is None,
            "summary": b["summary"] if b else None,
            "product": b["product"] if b else None, "component": b["component"] if b else None,
            "type": b.get("type") if b else None, "severity": b.get("severity") if b else None,
            "status": b["status"] if b else None, "resolution": b.get("resolution") if b else None,
            "keywords": b.get("keywords", []) if b else [],
            "regressed_by": b.get("regressed_by", []) if b else [],
            "status_n": b.get(status_f) if b else None, "tracking_n": b.get(tracking_f) if b else None,
            "landings": len(landings), "reverts": len(reverts), "in_tree": in_tree,
            "files_net_changed": net, "files_touched": len(touched), "train_hop": train_hop,
            "rides_with_train": riding[:8] if train_hop == "partly" else [],
            "commits": [c["sha"] for c in cs],
            "last_landing": last.isoformat() if last else None,
            "days_before_end": round((end_date - last).total_seconds() / 86400, 1) if last else None,
            "lines": lines, "files": sum(c["files"] for c in landings),
            "areas": dict(areas.most_common(5)),
            "regressions": regs,
        })
    records.sort(key=lambda r: int(r["id"]))
    return {
        "version": n, "range": rev_range,
        "start_sha": git(repo, "rev-parse", "--short=12", start).strip(),
        "end_sha": git(repo, "rev-parse", "--short=12", end).strip(),
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "late_days": args.late_days,
        "authenticated": bool(api_key()),
        "commit_count": len(commits),
        "no_bug_commits": [c for c in commits if not c["bug"]],
        "restricted": restricted, "bugs": records,
        "commits": [{k: v for k, v in c.items() if k != "paths"} for c in commits],
    }


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def comp(r):
    return f"{r['product']} :: {r['component']}" if r["product"] else "(restricted)"


def cycle_regs(b):
    return [r for r in b["regressions"] if not r.get("before_cycle")]


def capped(title, items, top):
    shown = items[:top]
    note = "all listed" if len(shown) == len(items) else f"top {len(shown)} of {len(items)}"
    return shown, [f"## {title} ({note})", ""]


def summarize(data, top):
    bugs = data["bugs"]
    visible = [b for b in bugs if not b["restricted"]]
    fixed = [b for b in visible if b["resolution"] == "FIXED"]
    commits = data["commits"]
    pre = sum(1 for b in bugs for r in b["regressions"] if r.get("before_cycle"))
    out = [f"# Firefox {data['version']} Nightly cycle: raw summary", "",
           f"- Range: `{data['range']}` ({data['start_sha']}..{data['end_sha']}), "
           f"{data['start_date'][:10]} to {data['end_date'][:10]}",
           f"- Commits (non-merge): {data['commit_count']}; reverts/backouts: "
           f"{sum(c['revert'] for c in commits)}; without a bug number: {len(data['no_bug_commits'])}",
           *([f"- **Unauthenticated run** ({data['auth_problem']}): security-restricted bugs are "
              "missing. See the setup steps printed by the script, or the skill's Step 0.", ]
             if data.get("auth_problem") else []),
           f"- Bugs referenced: {len(bugs)}; visible: {len(visible)}; "
           f"security-restricted or missing: {len(data['restricted'])}"
           f"{'' if data['authenticated'] else ' (unauthenticated run)'}",
           f"- Visible bugs currently RESOLVED FIXED: {len(fixed)}; other states: "
           f"{len(visible) - len(fixed)}",
           "- Line counts are added+deleted, excluding revert commits. `code` excludes test and "
           "vendored/generated paths (path heuristics, so check large entries).",
           f"- Regression counts only include regressions filed after the cycle started; "
           f"{pre} older ones are excluded here but kept in cycle.json (`before_cycle`).",
           "- `In tree` compares each bug's files between the cycle's start and end: `no` means none "
           "of them changed over the cycle, so the work was backed out (or fully undone) before "
           "merge and is not in this release. `mostly-not` means under half changed (other bugs touching the same files keep this above 0).",
           f"- `Hop` is for New Tab add-on code (`{TRAIN_HOP_PREFIX}` and its strings), which ships "
           f"through Train Hop independently of the release train. `yes`: all its product code is "
           f"in the add-on ({sum(b['train_hop'] == 'yes' for b in bugs)} bugs, "
           f"{sum(b['lines']['code'] for b in bugs if b['train_hop'] == 'yes')} code lines). "
           f"`partly`: it also changes parts that ride the train, such as prefs in firefox.js, "
           f"FeatureManifest.yaml or metrics.yaml, listed in cycle.json as `rides_with_train` "
           f"({sum(b['train_hop'] == 'partly' for b in bugs)} bugs).",
           "- Capped tables are ranked; the complete data is in cycle.json.", ""]

    prod = collections.defaultdict(lambda: [0, 0, 0])
    for b in bugs:
        p = prod[b["product"] or "(restricted)"]
        p[0] += 1
        p[1] += b["lines"]["code"]
        p[2] += len(cycle_regs(b))
    out += ["## By product (all listed)", ""]
    out += table(["Product", "Bugs", "Code lines", "Regressions filed"],
                 [(k, *v) for k, v in sorted(prod.items(), key=lambda kv: -kv[1][0])]) + [""]

    cmp = collections.defaultdict(lambda: [0, 0, 0, 0])
    for b in bugs:
        c = cmp[comp(b)]
        c[0] += 1
        c[1] += b["lines"]["code"]
        c[2] += b["reverts"] > 0
        c[3] += len(cycle_regs(b))
    rows, head = capped("By component, by bug count",
                        sorted(cmp.items(), key=lambda kv: (-kv[1][0], -kv[1][1])), top)
    out += head + table(["Component", "Bugs", "Code lines", "Backed out", "Regressions filed"],
                        [(k, *v) for k, v in rows]) + [""]

    areas = collections.Counter()
    area_bugs = collections.defaultdict(set)
    for b in bugs:
        for a, v in b["areas"].items():
            areas[a] += v
            area_bugs[a].add(b["id"])
    rows, head = capped("By source directory, by code lines", areas.most_common(), top)
    out += head + table(["Directory", "Code lines", "Bugs"],
                        [(a, v, len(area_bugs[a])) for a, v in rows]) + [""]

    types = collections.Counter(b["type"] for b in visible)
    reg_fixes = [b for b in visible if "regression" in b["keywords"]]
    out += ["## Bug types", "",
            ", ".join(f"{k}: {v}" for k, v in types.most_common()) + ".",
            f"Fixes for bugs carrying the `regression` keyword: {len(reg_fixes)}.", ""]

    with_regs = [b for b in bugs if cycle_regs(b)]
    with_regs.sort(key=lambda b: (-sum(r.get("open", False) for r in cycle_regs(b)),
                                  worst_severity([r for r in cycle_regs(b) if r.get("open")]),
                                  -len(cycle_regs(b))))
    out += [f"## Cycle bugs with regressions filed against them ({len(with_regs)}, all listed)", ""]
    out += table(["Bug", "Component", "Open/total", "Worst open sev", "Regressions", "Summary"],
                 [(b["id"], comp(b),
                   f"{sum(r.get('open', False) for r in cycle_regs(b))}/{len(cycle_regs(b))}",
                   worst_severity([r for r in cycle_regs(b) if r.get("open")]),
                   ", ".join(f"{r['id']}{'*' if r.get('open') else ''}" for r in cycle_regs(b)),
                   (b["summary"] or "")[:70]) for b in with_regs]) + ["", "`*` = still open", ""]

    backed = sorted([b for b in bugs if b["reverts"]],
                    key=lambda b: (b["resolution"] == "FIXED", -b["reverts"], -b["lines"]["code"]))
    rows, head = capped("Bugs backed out at least once, not-fixed first, then by backouts",
                        backed, top)
    out += head + table(["Bug", "Component", "Backouts", "Landings", "In tree", "Status",
                         "Code lines", "Summary"],
                        [(b["id"], comp(b), b["reverts"], b["landings"], b["in_tree"],
                          f"{b['status']} {b['resolution'] or ''}".strip() if b["status"] else "--",
                          b["lines"]["code"], (b["summary"] or "")[:70]) for b in rows]) + [""]

    gone = sorted([b for b in bugs if b["reverts"] and b["in_tree"] in ("no", "mostly-not")],
                  key=lambda b: -b["lines"]["code"])
    rows, head = capped("Backed out and not in the release (net change absent), by size",
                        gone, top)
    out += head + table(["Bug", "Component", "In tree", "Backouts", "Code lines", "Summary"],
                        [(b["id"], comp(b), b["in_tree"], b["reverts"], b["lines"]["code"],
                          (b["summary"] or "")[:70]) for b in rows]) + [""]

    rows, head = capped("Largest code changes",
                        sorted(bugs, key=lambda b: -b["lines"]["code"]), top)
    out += head + table(["Bug", "Component", "Code lines", "Files", "Days before end",
                         "In tree", "Hop", "Main area", "Summary"],
                        [(b["id"], comp(b), b["lines"]["code"], b["files"], b["days_before_end"],
                          b["in_tree"], b["train_hop"],
                          next(iter(b["areas"]), "--"), (b["summary"] or "")[:70])
                         for b in rows]) + [""]

    late = sorted([b for b in bugs if b["days_before_end"] is not None
                   and b["days_before_end"] <= data["late_days"] and b["lines"]["code"] >= 100],
                  key=lambda b: -b["lines"]["code"])
    rows, head = capped(f"Late landings: last landing within {data['late_days']} days of the "
                        f"merge, >= 100 code lines, by size", late, top)
    out += head + table(["Bug", "Component", "Code lines", "Days before end", "Backouts",
                         "In tree", "Hop", "Summary"],
                        [(b["id"], comp(b), b["lines"]["code"], b["days_before_end"], b["reverts"],
                          b["in_tree"], b["train_hop"],
                          (b["summary"] or "")[:70]) for b in rows]) + [""]

    flagged = sorted([b for b in visible if b["severity"] in ("S1", "S2")
                      or b["tracking_n"] in ("+", "blocking")],
                     key=lambda b: (b["severity"] or "S9", -b["lines"]["code"]))
    rows, head = capped(f"S1/S2 or tracked for {data['version']}, by severity then size",
                        flagged, top)
    out += head + table(["Bug", "Component", "Severity", f"tracking-{data['version']}", "Type",
                         "Summary"],
                        [(b["id"], comp(b), b["severity"], b["tracking_n"] or "--", b["type"],
                          (b["summary"] or "")[:70]) for b in rows]) + [""]

    unfixed = [b for b in visible if b["resolution"] != "FIXED"]
    out += [f"## Landed but not currently RESOLVED FIXED ({len(unfixed)}, all listed)", ""]
    out += table(["Bug", "Component", "Status", "Backouts", "Summary"],
                 [(b["id"], comp(b), f"{b['status']} {b['resolution'] or ''}".strip(),
                   b["reverts"], (b["summary"] or "")[:70]) for b in unfixed]) + [""]

    out += [f"## Restricted or missing bugs ({len(data['restricted'])})", "",
            ", ".join(data["restricted"]) or "none", ""]
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--version", type=int, required=True, help="Firefox version of the Nightly cycle")
    p.add_argument("--range", help="explicit START..END (default: the FIREFOX_NIGHTLY_*_END tags)")
    p.add_argument("--repo", help="Gecko checkout (default: as saved by watchlist.py check-setup)")
    p.add_argument("--outdir", help="default: <tmp>/relman-cycle-review/cycle-<version>, "
                                     "or $RELMAN_CYCLE_OUTDIR/cycle-<version>")
    p.add_argument("--top", type=int, default=25, help="rows in the capped tables")
    p.add_argument("--late-days", type=float, default=3, help="window for the late-landing table")
    args = p.parse_args()
    args.repo = str(setup_check.resolve_repo(args.repo))
    outdir = Path(args.outdir).expanduser() if args.outdir else setup_check.default_outdir(args.version)
    outdir.mkdir(parents=True, exist_ok=True)

    global KEY
    KEY, problem = setup_check.bugzilla_status()
    setup_check.report_bugzilla(problem)
    data = build(args)
    data["auth_problem"] = problem
    (outdir / "cycle.json").write_text(json.dumps(data, indent=1))
    (outdir / "summary.md").write_text(summarize(data, args.top))
    print(f"wrote {outdir / 'summary.md'} and {outdir / 'cycle.json'}")
    print(f"{data['commit_count']} commits, {len(data['bugs'])} bugs, "
          f"{len(data['restricted'])} restricted")


if __name__ == "__main__":
    main()
