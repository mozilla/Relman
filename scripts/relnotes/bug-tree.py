#!/usr/bin/env python3
"""Cluster a window's landings into features, so a release note covers the feature, not the bug.

Work fans out: a New Tab widget can be 6-7 bugs, a desktop theming refresh 15+. One note per bug
would be wrong even if every individual bug were noteworthy. This script groups the survivors from
scan-window.py into feature-shaped clusters using five independent signals, any of which can catch
a feature the others miss:

  meta        shared meta-bug ancestor (walks `blocks` upward)
  whiteboard  shared project tag, e.g. [fidefe1234] or [sng]
  prefix      shared bracketed summary prefix, e.g. "[Google Lens Fenix]"
  path        shared source subtree across the landings (a directory getting 15 commits
              is a feature push even with no meta bug filed)
  pref        shared preference namespace, e.g. browser.smartwindow.*

For every meta-backed cluster it also runs a **completeness check**: the meta bug's *full*
dependency list, not just the in-window members. That is what distinguishes "ready to note now"
from "still cooking", and it catches features whose work began before the window opened.

Input is scan-window.py's JSON. Read-only; hits Bugzilla for parent/dependency metadata only.

Usage:
  scan-window.py --cycle 155 --version 155 --format json -o /tmp/w.json
  bug-tree.py --input /tmp/w.json
  bug-tree.py --input /tmp/w.json --min-cluster 3
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

# Cap on parent bugs fetched, which protects a full-cycle run.
MAX_PARENTS = 600
# A subtree with more members than this is a directory, not a feature.
MAX_PATH_CLUSTER = 12

# Whiteboard project tags: [fidefe1234], [sng], [omc], [fxdroid] ...
WHITEBOARD_TAG_RE = re.compile(r"\[([a-zA-Z][\w:.\- ]{1,40})\]")
# A bracketed prefix on the summary is a strong hand-rolled feature marker.
SUMMARY_PREFIX_RE = re.compile(r"^\s*\[([^\]]{2,40})\]")
# Whiteboard tags that are process bookkeeping, not features.
TAG_STOPLIST = {
    "meta", "fixed", "wip", "good first bug", "lang=js", "lang=c++", "lang=rust",
    "necko-triaged", "fxperf", "qa-not-actionable", "sec-triaged", "geckoview:mr",
    "not-a-fission-bug", "fission", "priority", "triaged", "backlog", "access-s2",
}
# Directory prefixes too generic to indicate a shared feature.
PATH_STOPLIST = {
    "browser", "toolkit", "dom", "layout", "gfx", "js", "netwerk", "widget", "modules",
    "testing", "third_party", "taskcluster", "mobile", "devtools", "services", "docshell",
    "accessible", "security", "xpcom", "python", "tools", "build",
}


def files_by_commit(repo: Path, start: str, end: str) -> dict[str, list[str]]:
    """One git call for the whole window: {sha12: [paths]}. Empty on failure -- path clustering is
    best-effort and must not abort."""
    log = trainlib.git(repo, "log", f"{start}..{end}", "--name-only", "--format=@@%H", check=False)
    out: dict[str, list[str]] = {}
    cur = None
    for line in log.splitlines():
        if line.startswith("@@"):
            cur = line[2:14]
            out[cur] = []
        elif line.strip() and cur:
            out[cur].append(line.strip())
    return out


def subtrees(path: str, min_depth: int = 2, max_depth: int = 5) -> list[str]:
    """Every candidate subtree for a path, shallow to deep.

    A single fixed depth cannot work: `browser/components/newtab` is a feature while
    `mobile/android/fenix` at the same depth is an entire application. So emit all
    depths and let the size filter downstream pick the level that behaves like a
    feature.
    """
    parts = path.split("/")
    return ["/".join(parts[:d]) for d in range(min_depth, min(max_depth, len(parts) - 1) + 1)]


# A meta whose only remaining dependencies are test work is functionally complete.
# Counting those against completeness understates readiness and produces a "hold the
# note" verdict for a finished feature: WebTransport read 7/9 with both open bugs being
# "Update wpt pass/fail expectations" and "Add xpcshell tests".
TEST_ONLY_DEP_RE = re.compile(
    r"\b(wpt|web-platform-test|mochitest|xpcshell|gtest|reftest|crashtest)\b"
    r"|\btest(s|ing)?\b.*\b(add|update|expectation|coverage|enable|disable)\b"
    r"|\b(add|update|write|enable|disable|fix)\b.*\btests?\b"
    r"|\bexpectations?\b",
    re.IGNORECASE,
)


def main() -> None:
    p = argparse.ArgumentParser(description="Cluster window survivors into features.")
    p.add_argument("--input", required=True, help="scan-window.py JSON output")
    p.add_argument("--repo", default=None,
                   help="Gecko checkout (default: saved by watchlist.py check-setup)")
    p.add_argument("--min-cluster", type=int, default=2, help="minimum members to report")
    p.add_argument("--max-meta-deps", type=int, default=60,
                   help="ignore meta bugs with more dependencies than this (tracking bugs)")
    args = p.parse_args()

    data = json.loads(Path(args.input).read_text())
    survivors = list(data["survivors"])
    by_id = {s["bug"]: s for s in survivors}
    window = data["window"]
    print(f"# clustering {len(survivors)} bugs from {window['start_desc']} .. "
          f"{window['end_desc']}", file=sys.stderr)

    # ---- signal 1+: gather parent bugs (candidate metas) -------------------
    parent_ids: set[str] = set()
    for s in survivors:
        for b in s.get("blocks", []):
            parent_ids.add(str(b))
    parent_ids -= set(by_id)
    if len(parent_ids) > MAX_PARENTS:
        print(f"# warning: {len(parent_ids)} parent bugs exceeds the cap of {MAX_PARENTS}; "
              "sampling the most-referenced", file=sys.stderr)
        # Counted over the candidates only. Counting every `blocks` target instead lets the ranking
        # spend slots on bugs that are themselves survivors -- which the line above deliberately
        # excluded -- and each wasted slot costs a real parent: on the 155 cycle, 82 of 600 went that
        # way and dropped 113 parents where 31 was the arithmetic minimum.
        freq = collections.Counter()
        for s in survivors:
            for b in s.get("blocks", []):
                if str(b) in parent_ids:
                    freq[str(b)] += 1
        kept = {b for b, _ in freq.most_common(MAX_PARENTS)}
        # Named, for the same reason the skipped metas are: a dropped parent is a cluster that never
        # existed, and a count alone leaves nobody able to tell which feature went missing. These are
        # the least-referenced, so they are the likeliest to be incidental -- but that is a
        # probability, not a guarantee.
        dropped = sorted(parent_ids - kept, key=lambda b: (-freq[b], b))
        print(f"#   dropped {len(dropped)} least-referenced parent(s): "
              + ", ".join(f"{b} ({freq[b]}x)" for b in dropped[:10])
              + (f", ... and {len(dropped) - 10} more" if len(dropped) > 10 else ""),
              file=sys.stderr)
        parent_ids = kept

    parents = trainlib.bugzilla_bugs(
        sorted(parent_ids), "id,summary,keywords,status,resolution,depends_on,blocks,whiteboard",
        label="related bugs")
    all_metas = {i: b for i, b in parents.items() if trainlib.is_meta(b)}
    # A meta with hundreds of dependencies is a standing tracking bug ("[meta] WebRTC
    # bugs"), not a feature. Clustering on it would sweep unrelated work together and
    # its completeness check would fetch thousands of bugs for no benefit.
    metas = {i: b for i, b in all_metas.items()
             if len(b.get("depends_on") or []) <= args.max_meta_deps}
    # Named, not just counted. A count cannot be audited: meta 2017363 (Interop 2026 WebRTC, 62
    # deps) was dropped here during the cycle-155 rollup and its absence had to be noticed by hand.
    # Closest to the threshold first and capped, because a cycle pass skips ~70 of these and the ones
    # worth a second look are the near misses -- nothing with 2,527 dependencies is a feature.
    skipped_metas = sorted(set(all_metas) - set(metas),
                           key=lambda i: len(all_metas[i].get("depends_on") or []))
    print(f"# {len(parents)} parents fetched, {len(all_metas)} are meta bugs "
          f"({len(skipped_metas)} skipped as broad tracking bugs with more than "
          f"{args.max_meta_deps} dependencies)", file=sys.stderr)
    for i in skipped_metas[:8]:
        deps = len(all_metas[i].get("depends_on") or [])
        print(f"#   skipped meta {i} ({deps} deps): {all_metas[i].get('summary','')[:66]}",
              file=sys.stderr)
    if len(skipped_metas) > 8:
        print(f"#   ... and {len(skipped_metas) - 8} broader ones. Raise --max-meta-deps to cluster "
              "any of these.", file=sys.stderr)

    clusters: list[dict] = []

    # meta clusters
    meta_members: dict[str, list[str]] = collections.defaultdict(list)
    for s in survivors:
        for b in s.get("blocks", []):
            if str(b) in metas:
                meta_members[str(b)].append(s["bug"])
    for meta_id, members in meta_members.items():
        clusters.append({
            "signal": "meta",
            "key": f"bug {meta_id}",
            "label": metas[meta_id].get("summary", ""),
            "meta_bug": meta_id,
            "members": sorted(set(members)),
        })

    # whiteboard tag clusters
    tag_members: dict[str, list[str]] = collections.defaultdict(list)
    for s in survivors:
        for tag in WHITEBOARD_TAG_RE.findall(s.get("whiteboard", "") or ""):
            t = tag.strip().lower()
            if t in TAG_STOPLIST or len(t) < 3:
                continue
            tag_members[t].append(s["bug"])
    for tag, members in tag_members.items():
        clusters.append({
            "signal": "whiteboard", "key": f"[{tag}]", "label": f"whiteboard tag [{tag}]",
            "meta_bug": None, "members": sorted(set(members)),
        })

    # summary-prefix clusters
    prefix_members: dict[str, list[str]] = collections.defaultdict(list)
    for s in survivors:
        m = SUMMARY_PREFIX_RE.match(s.get("summary", ""))
        if m:
            key = m.group(1).strip().lower()
            if key not in TAG_STOPLIST:
                prefix_members[key].append(s["bug"])
    for key, members in prefix_members.items():
        clusters.append({
            "signal": "prefix", "key": f"[{key}]", "label": f"summary prefix [{key}]",
            "meta_bug": None, "members": sorted(set(members)),
        })

    # path clusters
    repo = trainlib.resolve_repo(args.repo)
    path_members: dict[str, set[str]] = collections.defaultdict(set)
    fmap = files_by_commit(repo, window["start"], window["end"])
    for s in survivors:
        for landing in s.get("landings", []):
            for path in fmap.get(landing["sha"], []):
                for st in subtrees(path):
                    path_members[st].add(s["bug"])
    # Keep the subtrees that behave like a feature: big enough to be a cluster,
    # small enough not to be "an entire application". Then collapse
    # parent/child levels that describe the identical set of bugs, keeping the
    # most specific path.
    lo = max(3, args.min_cluster)
    sized = {st: m for st, m in path_members.items() if lo <= len(m) <= MAX_PATH_CLUSTER}
    by_members: dict[frozenset, str] = {}
    for st, members in sorted(sized.items(), key=lambda x: -x[0].count("/")):
        by_members.setdefault(frozenset(members), st)
    for members, st in by_members.items():
        clusters.append({
            "signal": "path", "key": st, "label": f"source subtree {st}/",
            "meta_bug": None, "members": sorted(members),
        })
    oversized = sorted(
        ((st, len(m)) for st, m in path_members.items() if len(m) > MAX_PATH_CLUSTER),
        key=lambda x: -x[1],
    )
    if oversized:
        # Name the biggest few, but give the total: this line is the only record of what was
        # excluded from clustering, and without a denominator it reads as the whole list.
        shown = oversized[:8]
        more = f", and {len(oversized) - len(shown)} more" if len(oversized) > len(shown) else ""
        print(f"# directory-level, too broad to be one feature, not clustered "
              f"({len(oversized)} director{'y' if len(oversized) == 1 else 'ies'}): "
              + ", ".join(f"{st} ({n})" for st, n in shown) + more, file=sys.stderr)

    # pref-namespace clusters (from landing subjects mentioning a dotted pref)
    pref_members: dict[str, set[str]] = collections.defaultdict(set)
    pref_re = re.compile(r"\b((?:browser|dom|layout|network|media|gfx|privacy|security)"
                         r"(?:\.[a-zA-Z0-9_-]+){2,})")
    for s in survivors:
        blob = s.get("summary", "") + " " + " ".join(
            l["subject"] for l in s.get("landings", [])
        )
        for pref in pref_re.findall(blob):
            ns = ".".join(pref.split(".")[:3])
            pref_members[ns].add(s["bug"])
    for ns, members in pref_members.items():
        if len(members) >= max(2, args.min_cluster):
            clusters.append({
                "signal": "pref", "key": ns, "label": f"preference namespace {ns}.*",
                "meta_bug": None, "members": sorted(members),
            })

    # ---- filter, dedupe, and merge overlapping evidence -------------------
    clusters = [c for c in clusters if len(c["members"]) >= args.min_cluster]
    merged: dict[frozenset, dict] = {}
    for c in clusters:
        key = frozenset(c["members"])
        if key in merged:
            merged[key]["signals"].append(f"{c['signal']}:{c['key']}")
            if c["meta_bug"] and not merged[key]["meta_bug"]:
                merged[key]["meta_bug"] = c["meta_bug"]
                merged[key]["label"] = c["label"]
        else:
            merged[key] = {
                "signals": [f"{c['signal']}:{c['key']}"],
                "label": c["label"],
                "meta_bug": c["meta_bug"],
                "members": c["members"],
            }
    # Rank by evidence quality, not raw size. Corroboration by several independent
    # signals is the strongest indicator; a filed meta bug means someone already
    # decided this was one body of work; a shared directory is the weakest signal
    # because directories exist for reasons other than features.
    SIGNAL_WEIGHT = {"meta": 4, "whiteboard": 3, "prefix": 3, "pref": 2, "path": 1}

    def rank(c: dict) -> tuple:
        kinds = {s.split(":", 1)[0] for s in c["signals"]}
        best = max(SIGNAL_WEIGHT.get(k, 0) for k in kinds)
        return (-len(kinds), -best, -len(c["members"]), c["label"])

    final = sorted(merged.values(), key=rank)

    # ---- completeness check for meta-backed clusters ----------------------
    dep_ids: set[str] = set()
    for c in final:
        if c["meta_bug"]:
            for d in metas[c["meta_bug"]].get("depends_on", []):
                dep_ids.add(str(d))
    dep_ids -= set(by_id) | set(parents)
    deps = trainlib.bugzilla_bugs(sorted(dep_ids), "id,status,resolution,summary",
                                  label="related bugs")
    known = {**parents, **deps}
    for c in final:
        if not c["meta_bug"]:
            continue
        all_deps = [str(d) for d in metas[c["meta_bug"]].get("depends_on", [])]
        resolved = open_ = unknown = open_test_only = 0
        open_substantive = []
        for d in all_deps:
            b = known.get(d)
            if d in by_id or (b and b.get("resolution") in ("FIXED", "WONTFIX", "DUPLICATE",
                                                            "INVALID")):
                resolved += 1
            elif b is None:
                unknown += 1
            else:
                open_ += 1
                if TEST_ONLY_DEP_RE.search(b.get("summary", "")):
                    open_test_only += 1
                else:
                    open_substantive.append((d, b.get("summary", "")))
        c["completeness"] = {
            "total_dependencies": len(all_deps),
            "resolved": resolved,
            "open": open_,
            "open_test_only": open_test_only,
            "open_substantive": open_substantive,
            "unknown": unknown,
            "in_window": len(c["members"]),
            "pct_resolved": round(100.0 * resolved / len(all_deps), 1) if all_deps else None,
        }

    lines = [
        f"Feature clusters: {window['start_desc']} .. {window['end_desc']}",
        f"{len(survivors)} bugs considered -> {len(final)} clusters "
        f"(min {args.min_cluster} members)",
        "",
        "One release note per cluster, not per bug. A cluster grouped by several",
        "independent signals is a stronger feature candidate than a single-signal one.",
        "",
    ]
    for c in final:
        lines.append(f"== {c['label'][:110]}")
        lines.append(f"   signals: {', '.join(c['signals'])}")
        comp = c.get("completeness")
        if comp:
            pct = f"{comp['pct_resolved']}%" if comp["pct_resolved"] is not None else "n/a"
            lines.append(
                f"   completeness: {comp['resolved']}/{comp['total_dependencies']} "
                f"dependencies resolved ({pct}), {comp['open']} still open"
                + (f", {comp['unknown']} unreadable" if comp["unknown"] else "")
            )
            if comp["open"] == 0 and comp["total_dependencies"]:
                lines.append("   -> feature looks COMPLETE; a note is in scope now")
            elif comp["open"] and not comp["open_substantive"]:
                lines.append(f"   -> FUNCTIONALLY COMPLETE: all {comp['open']} remaining "
                             "dependencies are test-only work; a note is in scope now")
            elif comp["open"]:
                lines.append(f"   -> still in progress; {len(comp['open_substantive'])} "
                             "substantive bug(s) open"
                             + (f" (+{comp['open_test_only']} test-only)"
                                if comp["open_test_only"] else ""))
                for d, summ in comp["open_substantive"][:3]:
                    lines.append(f"        open: {d} {summ[:70]}")
            if comp["in_window"] < comp["total_dependencies"]:
                lines.append(
                    f"   -> {comp['total_dependencies'] - comp['in_window']} dependencies "
                    "landed OUTSIDE this window; the feature predates it"
                )
        lines.append(f"   {len(c['members'])} bugs in window:")
        for b in c["members"]:
            s = by_id.get(b, {})
            lines.append(f"     {b}  {s.get('component', '?')}: {s.get('summary', '')[:90]}")
        lines.append("")
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"error: {e}")
