#!/usr/bin/env python3
"""Collect the non-Bugzilla signals for a Nightly cycle review: pref defaults, Nimbus, perf, crashes.

Run after cycle-data.py, whose cycle.json supplies the window and the bug-to-regression map.
Writes <outdir>/signals.json and <outdir>/signals.md.

Usage:
  cycle-signals.py --version 159
  cycle-signals.py --version 159 --skip crashes
"""

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup_check  # noqa: E402

USER_AGENT = "relman-nightly-cycle-review"
BUGZILLA_REST = "https://bugzilla.mozilla.org/rest"
TREEHERDER = "https://treeherder.mozilla.org/api"
SUPERSEARCH = "https://crash-stats.mozilla.org/api/SuperSearch/"
SOCORRO_BUGS = "https://crash-stats.mozilla.org/api/Bugs/"
EXPERIMENTER = "https://experimenter.services.mozilla.com/api/v6/experiments/"
KEY = None
PERF_REPOS = {"autoland": 77, "mozilla-central": 1}
PERF_STATUS = {0: "untriaged", 1: "downstream", 2: "reassigned", 3: "invalid", 4: "improvement",
               5: "investigating", 6: "wontfix", 7: "fixed", 8: "backedout"}
PERF_OPEN = {"untriaged", "investigating", "downstream"}
MESSAGING_FEATURES = re.compile(r"^(fxms|featureCallout|messaging|microsurveys|legacyHeartbeat|"
                                r"infobar|aboutwelcome|backgroundTaskMessage|spotlight)")
PING_MIN_COUNT = 20
INTERMITTENT_MIN = 30
MANIFEST_GLOBS = ["*.toml", "*.ini", "*.list"]
CONDITION_RE = re.compile(r"(skip-if|fail-if|run-if|fails-if|random-if|skip\b|\bos\s*==|debug|asan|tsan|"
                          r"verify|fission|ccov|android|win|mac|linux)")
NOT_TEST_MANIFEST_RE = re.compile(r"(^third_party/|(^|/)(Cargo|pyproject|rust-toolchain|"
                                  r"taplo|ruff|deny|clippy|rustfmt)\.toml$|^supply-chain/|"
                                  r"^\.cargo/|^python/|^taskcluster/)")
BUG_REF_RE = re.compile(r"[Bb]ug\s*(\d{6,8})")
OPEN_PRODUCTS = ["Core", "Firefox", "Toolkit", "Firefox for Android", "GeckoView", "DevTools",
                 "WebExtensions", "NSS"]
REPORTER_EXCLUSIONS = [("notequals", "%group.mozilla-corporation%"),
                       ("notequals", "%group.editbugs%"),
                       ("notsubstring", "@mozilla.bugs"), ("notsubstring", ".tld")]
BULK_REPORTER_MIN = 5
CRASH_FACETS = 400
CRASH_MIN_INSTALLS = 10
CRASH_CANDIDATES = 40


def fetch_json(url, headers=None):
    req_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    last = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = RuntimeError(f"HTTP {e.code} {e.reason} for {url[:160]}")
            if e.code < 500 and e.code != 429:
                raise last from e
        except (urllib.error.URLError, TimeoutError) as e:
            last = RuntimeError(f"could not reach {url[:80]}: {getattr(e, 'reason', e)}")
        if attempt < 3:
            time.sleep(2 ** attempt)
    raise last


def bugzilla_headers():
    return {"X-BUGZILLA-API-KEY": KEY} if KEY else {}


def regression_owner(cycle):
    """Map each regression bug id to the cycle bug(s) it was filed against."""
    owner = collections.defaultdict(list)
    for b in cycle["bugs"]:
        for r in b["regressions"]:
            owner[str(r["id"])].append(b["id"])
    return owner


def collect_prefs(args, cycle):
    script = Path(args.relman).expanduser() / "scripts/relnotes/pref-delta.py"
    if not script.is_file():
        return {"error": f"{script} not found; pass --relman"}
    run = subprocess.run(
        [sys.executable, str(script), "--repo", str(Path(args.repo).expanduser()),
         "--range", cycle["range"], "--rev", cycle["range"].split("..")[1], "--no-fetch",
         "--format", "json"], capture_output=True, text=True)
    if run.returncode:
        return {"error": run.stderr.strip()[-500:]}
    rows = []
    for c in json.loads(run.stdout)["changed"]:
        before, after = c["before_by_config"], c["after_by_config"]
        riding = sorted(k for k in after if after[k] != before.get(k)
                        and k.split("/")[0] in ("beta", "beta-late", "release"))
        rel_before = {k: before.get(k) for k in riding}
        rel_after = {k: after[k] for k in riding}
        if not riding:
            kind = "nightly-or-early-beta-only"
        elif all(v is None for v in rel_after.values()):
            kind = "removed"
        elif all(v is None for v in rel_before.values()):
            kind = "new-off" if all(v in ("false", "0", '""', None) for v in rel_after.values()) \
                else "new-on"
        else:
            kind = "flipped"
        rows.append({"pref": c["pref"], "kind": kind, "before": c["before"], "after": c["after"],
                     "riding_configs": riding, "bugs": c["bugs"],
                     "commits": c["commits"][:3]})
    order = ["flipped", "new-on", "removed", "new-off", "nightly-or-early-beta-only"]
    rows.sort(key=lambda r: (order.index(r["kind"]), r["pref"]))
    return {"changes": rows}


def collect_nimbus(args, cycle):
    n = args.version
    live = []
    for e in fetch_json(EXPERIMENTER):
        if e.get("endDate") or e.get("appName") not in ("firefox_desktop", "fenix"):
            continue
        targeting = e.get("targeting") or ""
        mins = [v for v in re.findall(r"versionCompare\('(\d+)[^']*'\)\s*>=\s*0", targeting)]
        maxs = [v for v in re.findall(r"versionCompare\('(\d+)[^']*'\)\s*<\s*0", targeting)]
        min_v = max(map(int, mins)) if mins else None
        max_v = min(map(int, maxs)) if maxs else None
        if (min_v and min_v > n) or (max_v and max_v <= n):
            continue
        channel = e.get("channel") or ""
        if not channel:
            found = re.findall(r'update\.channel\s*==\s*"(\w+)"', targeting)
            channel = ",".join(sorted(set(found)))
        if channel and not {"beta", "release"} & set(channel.split(",")):
            continue
        live.append({"slug": e["slug"], "app": e["appName"], "rollout": e.get("isRollout"),
                     "channel": channel, "features": e.get("featureIds", []),
                     "min_version": min_v, "start": e.get("startDate"),
                     "paused": e.get("isEnrollmentPaused"),
                     "population": (e.get("bucketConfig") or {}).get("count"),
                     "total": (e.get("bucketConfig") or {}).get("total"),
                     "new_for_n": min_v == n})
    end = cycle["end_date"][:10]
    recent_from = (datetime.fromisoformat(cycle["start_date"]) - (
        datetime.fromisoformat(cycle["end_date"]) - datetime.fromisoformat(cycle["start_date"])) * 2
    ).date().isoformat()
    for x in live:
        x["relevant"] = bool((x["min_version"] or 0) >= n - 1 or not x["start"]
                             or x["start"] >= recent_from)
    live.sort(key=lambda x: (not x["relevant"], not x["new_for_n"], not x["rollout"], x["slug"]))
    return {"live": live, "recent_from": recent_from, "as_of": end}


def ts(iso):
    return datetime.fromisoformat(iso).timestamp()


def collect_perf(args, cycle):
    start, end = ts(cycle["start_date"]), ts(cycle["end_date"])
    owner = regression_owner(cycle)
    out = []
    for repo, repo_id in PERF_REPOS.items():
        url = f"{TREEHERDER}/performance/alertsummary/?repository={repo_id}&limit=100"
        while url:
            page = fetch_json(url)
            done = False
            for s in page["results"]:
                if s["push_timestamp"] < start:
                    done = True
                    continue
                if s["push_timestamp"] >= end:
                    continue
                regs = [a for a in s["alerts"] if a["is_regression"]]
                imps = [a for a in s["alerts"] if not a["is_regression"]]
                top = sorted(regs or imps, key=lambda a: -a["amount_pct"])[:3]
                bug = str(s["bug_number"]) if s["bug_number"] else None
                out.append({
                    "id": s["id"], "repo": repo, "status": PERF_STATUS.get(s["status"], s["status"]),
                    "revision": s["revision"][:12], "bug": bug,
                    "cycle_bugs": owner.get(bug, []) if bug else [],
                    "regressions": len(regs), "improvements": len(imps),
                    "max_regression_pct": max((a["amount_pct"] for a in regs), default=0),
                    "max_improvement_pct": max((a["amount_pct"] for a in imps), default=0),
                    "top": [f"{a['series_signature']['test'] or a['series_signature']['suite']} "
                            f"({a['series_signature']['machine_platform']}) "
                            f"{'+' if a['is_regression'] else '-'}{a['amount_pct']:.1f}%"
                            for a in top],
                })
            url = None if done else page.get("next")
    out.sort(key=lambda s: (s["status"] not in PERF_OPEN, -s["regressions"], -s["max_regression_pct"]))
    return {"summaries": out}


def supersearch(params):
    return fetch_json(SUPERSEARCH + "?" + urllib.parse.urlencode(params, doseq=True))


def crash_window(build_lo, build_hi, date_lo, date_hi, extra=None):
    return [("product", "Firefox"), ("release_channel", "nightly"),
            ("build_id", f">={build_lo}"), ("build_id", f"<{build_hi}"),
            ("date", f">={date_lo}"), ("date", f"<{date_hi}"), *(extra or [])]


def signature_table(window):
    d = supersearch([*window, ("_facets", "signature"), ("_facets_size", CRASH_FACETS),
                     ("_aggs.signature", "_cardinality.install_time"), ("_results_number", 0)])
    table = {f["term"]: (f["count"], f["facets"]["cardinality_install_time"]["value"])
             for f in d["facets"]["signature"]}
    return d["total"], table


def collect_crashes(args, cycle):
    n = args.version
    start = datetime.fromisoformat(cycle["start_date"])
    end = datetime.fromisoformat(cycle["end_date"])
    span = end - start
    prev_start = start - span
    fmt_b, fmt_d = "%Y%m%d%H%M%S", "%Y-%m-%d"
    cur = crash_window(start.strftime(fmt_b), end.strftime(fmt_b),
                       start.strftime(fmt_d), end.strftime(fmt_d))
    prev = crash_window(prev_start.strftime(fmt_b), start.strftime(fmt_b),
                        prev_start.strftime(fmt_d), start.strftime(fmt_d))
    cur_total, cur_table = signature_table(cur)
    prev_total, prev_table = signature_table(prev)
    scale = prev_total / cur_total if cur_total else 1
    candidates = []
    for sig, (count, installs) in cur_table.items():
        if installs < CRASH_MIN_INSTALLS:
            continue
        p_count, p_installs = prev_table.get(sig, (None, None))
        if p_count is None:
            d = supersearch([*prev, ("signature", f"={sig}"),
                             ("_aggs.signature", "_cardinality.install_time"),
                             ("_facets", "signature"), ("_results_number", 0)])
            facet = d["facets"].get("signature", [])
            p_count = d["total"]
            p_installs = facet[0]["facets"]["cardinality_install_time"]["value"] if facet else 0
        ratio = (installs / p_installs) if p_installs else None
        kind = "new" if not p_installs else "rising" if ratio and ratio >= 3 else None
        if kind:
            candidates.append({"signature": sig, "kind": kind, "count": count, "installs": installs,
                               "prev_count": p_count, "prev_installs": p_installs,
                               "install_ratio": round(ratio, 1) if ratio else None})
        if len(candidates) >= CRASH_CANDIDATES:
            break
    last_build_cut = (end - span / 5).strftime(fmt_b)
    for c in candidates:
        d = supersearch([("product", "Firefox"), ("release_channel", "nightly"),
                         ("signature", f"={c['signature']}"), ("date", f">={start.strftime(fmt_d)}"),
                         ("_sort", "-build_id"), ("_columns", "build_id"), ("_results_number", 1)])
        hits = d.get("hits") or []
        c["last_build"] = str(hits[0]["build_id"]) if hits else None
        c["active_at_end"] = bool(c["last_build"] and c["last_build"] >= last_build_cut)
    if candidates:
        bugs = fetch_json(SOCORRO_BUGS + "?" + urllib.parse.urlencode(
            [("signatures", c["signature"]) for c in candidates], doseq=True)).get("hits", [])
        by_sig = collections.defaultdict(list)
        for h in bugs:
            by_sig[h["signature"]].append(str(h["id"]))
        ids = sorted({i for v in by_sig.values() for i in v}, key=int)
        info = {}
        for i in range(0, len(ids), 100):
            qs = urllib.parse.urlencode({"id": ",".join(ids[i:i + 100]),
                                         "include_fields": "id,status,resolution,regressed_by,"
                                                           f"cf_status_firefox{n},summary"})
            for b in fetch_json(f"{BUGZILLA_REST}/bug?{qs}", bugzilla_headers()).get("bugs", []):
                info[str(b["id"])] = b
        cycle_ids = {b["id"] for b in cycle["bugs"]}
        for c in candidates:
            c["bugs"] = []
            for i in by_sig.get(c["signature"], []):
                b = info.get(i)
                c["bugs"].append({
                    "id": i, "visible": b is not None,
                    "status": f"{b['status']} {b.get('resolution') or ''}".strip() if b else None,
                    "status_n": b.get(f"cf_status_firefox{n}") if b else None,
                    "regressed_by_cycle_bug": [str(r) for r in (b or {}).get("regressed_by", [])
                                               if str(r) in cycle_ids]})
    return {"window": {"current": [start.isoformat(), end.isoformat()],
                       "previous": [prev_start.isoformat(), start.isoformat()]},
            "totals": {"current": cur_total, "previous": prev_total, "scale": round(scale, 2)},
            "candidates": candidates}


def ping_table(version, date_from, date_to):
    run = subprocess.run(["socorro-cli", "--format", "json", "crash-pings", "--channel", "nightly",
                          "--version", version, "--from", date_from, "--to", date_to],
                         capture_output=True, text=True, timeout=1800,
                         env={**os.environ, "MOZTOOLS_UPDATE_CHECK": "0"})
    if run.returncode:
        raise RuntimeError(run.stderr.strip()[-300:])
    data = json.loads(run.stdout)
    return data["filtered_total"], {i["label"]: i["count"] for i in data["items"]}


def collect_crash_pings(args, cycle):
    n = args.version
    start = datetime.fromisoformat(cycle["start_date"])
    end = datetime.fromisoformat(cycle["end_date"])
    prev_start = start - (end - start)
    day = lambda d: d.strftime("%Y-%m-%d")
    cur_total, cur = ping_table(f"{n}.0a1", day(start), day(end))
    prev_total, prev = ping_table(f"{n - 1}.0a1", day(prev_start), day(start))
    rows = []
    for sig, count in sorted(cur.items(), key=lambda kv: -kv[1]):
        if count < PING_MIN_COUNT:
            continue
        share = count / cur_total
        p = prev.get(sig, 0)
        p_share = p / prev_total if prev_total else 0
        ratio = share / p_share if p_share else None
        kind = "new" if not p else "rising" if ratio >= 3 else None
        if kind:
            rows.append({"signature": sig, "kind": kind, "count": count, "share_pct": round(100 * share, 2),
                         "prev_count": p, "prev_share_pct": round(100 * p_share, 2),
                         "share_ratio": round(ratio, 1) if ratio else None})
    top = [{"signature": sig, "count": c, "share_pct": round(100 * c / cur_total, 2),
            "prev_share_pct": round(100 * prev.get(sig, 0) / prev_total, 2) if prev_total else None}
           for sig, c in sorted(cur.items(), key=lambda kv: -kv[1])[:15]]
    return {"totals": {"current": cur_total, "previous": prev_total}, "candidates": rows, "top": top,
            "window": {"current": [day(start), day(end)], "previous": [day(prev_start), day(start)]}}


def failure_counts(date_from, date_to):
    url = f"{TREEHERDER}/failures/?startday={date_from}&endday={date_to}&tree=trunk"
    return {str(r["bug_id"]): r["bug_count"] for r in fetch_json(url) if r["bug_id"]}


def collect_intermittents(args, cycle):
    start = datetime.fromisoformat(cycle["start_date"])
    end = datetime.fromisoformat(cycle["end_date"])
    prev_start = start - (end - start)
    day = lambda d: d.strftime("%Y-%m-%d")
    cur = failure_counts(day(start), day(end))
    prev = failure_counts(day(prev_start), day(start))
    cands = []
    for bug, count in cur.items():
        p = prev.get(bug, 0)
        if count >= INTERMITTENT_MIN and (not p or count / p >= 3):
            cands.append({"bug": bug, "count": count, "prev_count": p})
    info = {}
    ids = [c["bug"] for c in cands]
    for i in range(0, len(ids), 100):
        qs = urllib.parse.urlencode({"id": ",".join(ids[i:i + 100]),
                                     "include_fields": "id,summary,product,component,status,"
                                                       "resolution,creation_time,regressed_by"})
        for b in fetch_json(f"{BUGZILLA_REST}/bug?{qs}", bugzilla_headers()).get("bugs", []):
            info[str(b["id"])] = b
    cycle_ids = {b["id"] for b in cycle["bugs"]}
    for c in cands:
        b = info.get(c["bug"])
        c.update({"visible": b is not None,
                  "summary": b["summary"] if b else None,
                  "component": f"{b['product']} :: {b['component']}" if b else None,
                  "status": f"{b['status']} {b.get('resolution') or ''}".strip() if b else None,
                  "filed_in_cycle": bool(b and b["creation_time"] >= cycle["start_date"][:10]),
                  "regressed_by_cycle_bug": [str(r) for r in (b or {}).get("regressed_by", [])
                                             if str(r) in cycle_ids]})
    cands.sort(key=lambda c: (not c["regressed_by_cycle_bug"], -c["count"]))
    return {"totals": {"current": sum(cur.values()), "previous": sum(prev.values())},
            "candidates": cands}


def collect_test_disables(args, cycle):
    repo = Path(args.repo).expanduser()
    start, end = cycle["range"].split("..")
    diff = subprocess.run(["git", "-C", str(repo), "diff", "-U0", "--no-renames", start, end,
                           "--", *MANIFEST_GLOBS], capture_output=True, text=True,
                          errors="replace").stdout
    per_dir = collections.defaultdict(lambda: {"added": 0, "removed": 0, "bugs": set(), "files": set()})
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            if path and NOT_TEST_MANIFEST_RE.search(path):
                path = None
            continue
        if not path or line.startswith(("---", "@@")) or not line[:1] in "+-":
            continue
        body = line[1:]
        if not CONDITION_RE.search(body) or "=" not in body and not body.strip().startswith('"'):
            continue
        if path.startswith("testing/web-platform/meta/") or path.startswith("testing/web-platform/mozilla/meta/"):
            key = "testing/web-platform (expectations)"
        else:
            parts = path.split("/")
            key = ("/".join(parts[:3]) if len(parts) > 3 else "/".join(parts[:-1])) or "(root)"
        d = per_dir[key]
        d["added" if line[0] == "+" else "removed"] += 1
        d["files"].add(path)
        if line[0] == "+":
            d["bugs"].update(BUG_REF_RE.findall(body))
    rows = [{"area": k, "added": v["added"], "removed": v["removed"], "net": v["added"] - v["removed"],
             "files": len(v["files"]), "bugs": sorted(v["bugs"], key=int)[:10]}
            for k, v in per_dir.items()]
    rows.sort(key=lambda r: -r["net"])
    return {"areas": rows, "total_added": sum(r["added"] for r in rows),
            "total_removed": sum(r["removed"] for r in rows)}


def user_story_field(story, key):
    m = re.search(rf"^{re.escape(key)}:(.*)$", story or "", re.M)
    return m.group(1).strip() if m else None


def webcompat_com_reports(n, date_from, date_to):
    out = []
    for label in (f"Firefox {n}.0", f"Firefox Mobile {n}.0"):
        q = f'repo:webcompat/web-bugs created:{date_from}..{date_to} "{label}" in:body'
        run = subprocess.run(["gh", "api", "-X", "GET", "search/issues", "-f", f"q={q}",
                              "-f", "per_page=100", "--jq",
                              ".items[] | {number, title, created_at, html_url, body, "
                              "labels: [.labels[].name]}"],
                             capture_output=True, text=True)
        if run.returncode:
            raise RuntimeError(run.stderr.strip()[-200:])
        for line in run.stdout.splitlines():
            issue = json.loads(line)
            body = issue.pop("body") or ""
            field = lambda k: (re.search(rf"\*\*{k}\*\*: *(.*)", body) or [None, None])[1]
            issue.update({"domain": issue["title"].split(" - ")[0].strip(),
                          "problem": field("Problem type"), "os": field("Operating System"),
                          "other_browser": field("Tested Another Browser"),
                          "channel": (re.search(r"<li>channel: (\w+)</li>", body) or [None, None])[1]})
            out.append(issue)
    out = list({i["number"]: i for i in out}.values())
    per_domain = collections.Counter(i["domain"] for i in out)
    totals = {}
    for domain in per_domain:
        q = f'repo:webcompat/web-bugs created:{date_from}..{date_to} "{domain}" in:title'
        run = subprocess.run(["gh", "api", "-X", "GET", "search/issues", "-f", f"q={q}",
                              "--jq", ".total_count"], capture_output=True, text=True)
        totals[domain] = int(run.stdout.strip()) if run.returncode == 0 else None
        time.sleep(2.2)
    for issue in out:
        total = totals.get(issue["domain"])
        issue["other_version_reports"] = (max(total - per_domain[issue["domain"]], 0)
                                          if total is not None else None)
    return sorted(out, key=lambda i: i["created_at"])


def collect_webcompat(args, cycle):
    n = args.version
    date_from, date_to = cycle["start_date"][:10], cycle["end_date"][:10]
    cycle_ids = {b["id"] for b in cycle["bugs"]}
    fields = ("id,summary,product,component,status,resolution,keywords,regressed_by,creation_time,"
              f"cf_status_firefox{n},cf_status_firefox{n - 1},cf_user_story,severity")
    qs = urllib.parse.urlencode({"f1": "keywords", "o1": "substring", "v1": "webcompat",
                                 "f2": "creation_ts", "o2": "greaterthaneq", "v2": date_from,
                                 "f3": "creation_ts", "o3": "lessthan", "v3": date_to,
                                 "include_fields": fields, "limit": 0})
    bugs = fetch_json(f"{BUGZILLA_REST}/bug?{qs}", bugzilla_headers()).get("bugs", [])
    rows = []
    for b in bugs:
        story = b.get("cf_user_story")
        channels = user_story_field(story, "autowebcompat-repro-channels")
        from_cycle = [str(r) for r in b.get("regressed_by", []) if str(r) in cycle_ids]
        nightly_only = bool(channels) and "nightly" in channels and "stable" not in channels
        new_in_n = (b.get(f"cf_status_firefox{n}") == "affected"
                    and b.get(f"cf_status_firefox{n - 1}") == "unaffected")
        rows.append({"id": str(b["id"]), "summary": b["summary"],
                     "component": f"{b['product']} :: {b['component']}",
                     "status": f"{b['status']} {b.get('resolution') or ''}".strip(),
                     "platform_bug": "webcompat:platform-bug" in b["keywords"],
                     "site_report": "webcompat:site-report" in b["keywords"],
                     "from_cycle_bug": from_cycle, "repro_channels": channels,
                     "nightly_only_repro": nightly_only, "new_in_n": new_in_n,
                     "impact": user_story_field(story, "impact"),
                     "user_impact_score": user_story_field(story, "user-impact-score"),
                     "diagnosis_team": user_story_field(story, "diagnosis-team")})
    flagged = [r for r in rows if r["from_cycle_bug"] or r["nightly_only_repro"] or r["new_in_n"]]
    try:
        reports = webcompat_com_reports(n, date_from, date_to)
        reports_error = None
    except Exception as e:
        reports, reports_error = [], str(e)
    teams = collections.Counter(r["diagnosis_team"] for r in rows if r["diagnosis_team"])
    return {"bugzilla": rows, "flagged": flagged, "diagnosis_teams": dict(teams.most_common()),
            "webcompat_com": reports, "webcompat_com_error": reports_error}


def collect_open_issues(args, cycle):
    if not KEY:
        return {"skipped": "needs a Bugzilla API key: the reporter group filters "
                           "(%group.mozilla-corporation%, %group.editbugs%) only work when "
                           "authenticated"}
    n = args.version
    start = datetime.fromisoformat(cycle["start_date"])
    end = datetime.fromisoformat(cycle["end_date"])
    mid = start + (end - start) / 2
    day = lambda d: d.strftime("%Y-%m-%d")
    fields = ("id,creator,creator_detail,summary,product,component,severity,keywords,regressed_by,"
              f"status,groups,cf_status_firefox{n},cf_status_firefox{n - 1}")
    bugs = {}
    for product in OPEN_PRODUCTS:
        for lo, hi in ((start, mid), (mid, end)):
            params = [("f1", "creation_ts"), ("o1", "greaterthaneq"), ("v1", day(lo)),
                      ("f2", "creation_ts"), ("o2", "lessthan"), ("v2", day(hi)),
                      ("resolution", "---"), ("type", "defect"), ("product", product),
                      ("include_fields", fields), ("limit", 0)]
            for i, (op, value) in enumerate(REPORTER_EXCLUSIONS, 3):
                params += [(f"f{i}", "reporter"), (f"o{i}", op), (f"v{i}", value)]
            url = f"{BUGZILLA_REST}/bug?" + urllib.parse.urlencode(params)
            for b in fetch_json(url, bugzilla_headers()).get("bugs", []):
                bugs[str(b["id"])] = b
    cycle_ids = {b["id"] for b in cycle["bugs"]}
    kept, dropped = [], collections.Counter()
    for b in bugs.values():
        if "intermittent-failure" in b["keywords"] or "UI Tests" in b["component"]:
            dropped["tests"] += 1
            continue
        security = bool(b.get("groups")) or any(k.startswith("sec-") for k in b["keywords"])
        regressors = [str(r) for r in b.get("regressed_by", [])]
        kept.append({
            "id": str(b["id"]), "summary": b["summary"], "creator": b["creator"],
            "component": f"{b['product']} :: {b['component']}", "status": b["status"],
            "severity": b.get("severity"), "security": security,
            "rating": next((k for k in b["keywords"] if k.startswith("sec-")), None),
            "regression": "regression" in b["keywords"],
            "regressed_by_cycle_bug": [r for r in regressors if r in cycle_ids],
            "regressor_known": bool(regressors), "crash": "crash" in b["keywords"],
            "status_n": b.get(f"cf_status_firefox{n}"),
            "new_in_n": (b.get(f"cf_status_firefox{n}") == "affected"
                         and b.get(f"cf_status_firefox{n - 1}") == "unaffected")})
    per_reporter = collections.Counter(b["creator"] for b in kept)
    for b in kept:
        b["bulk_reporter"] = per_reporter[b["creator"]] >= BULK_REPORTER_MIN
    bulk = collections.defaultdict(collections.Counter)
    for b in kept:
        if b["bulk_reporter"]:
            bulk[b["creator"]][b["component"]] += 1
    return {"bugs": kept, "dropped": dict(dropped), "products": OPEN_PRODUCTS,
            "bulk_reporters": [{"bugs": sum(c.values()), "components": dict(c.most_common(3))}
                               for c in sorted(bulk.values(), key=lambda c: -sum(c.values()))],
            "reporter_rule": "reporter not in the mozilla-corporation or editbugs Bugzilla groups "
                             "(employees and trusted contributors), and not a bot account"}


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |" for row in rows]
    return out


def render(sig, n):
    out = [f"# Firefox {n} Nightly cycle: external signals", ""]
    setup = sig.get("_setup", {})
    if setup.get("auth_problem"):
        out += [f"**Unauthenticated run** ({setup['auth_problem']}): security bugs are missing "
                "and the user-report section is skipped. See the skill's Step 0.", ""]
    for what, fix in setup.get("missing_tools", []):
        out += [f"**{what} not collected:** {fix}.", ""]
    prefs = sig.get("prefs", {})
    if prefs and "failed" not in prefs:
        out += ["## Pref default changes (pref-delta.py)", ""]
    if "error" in prefs:
        out += [f"Not collected: {prefs['error']}", ""]
    elif "changes" in prefs:
        counts = collections.Counter(r["kind"] for r in prefs["changes"])
        out += ["Kinds, judged on Beta (late) and release configs: " +
                ", ".join(f"{k}: {v}" for k, v in counts.items()) + ". `new-off` and "
                "`nightly-or-early-beta-only` are listed in signals.json only.", ""]
        rows = [r for r in prefs["changes"] if r["kind"] in ("flipped", "new-on", "removed")]
        out += table(["Kind", "Pref", "Before", "After", "Bugs"],
                     [(r["kind"], f"`{r['pref']}`", r["before"], r["after"], ", ".join(r["bugs"]))
                      for r in rows]) + [""]
    nim = sig.get("nimbus", {})
    if "live" in nim:
        live = nim["live"]
        new = [e for e in live if e["new_for_n"]]
        rel = [e for e in live if e["relevant"]
               and not all(MESSAGING_FEATURES.match(f) for f in e["features"])]
        msg = [e for e in live if e["relevant"] and e not in rel]
        out += [f"## Nimbus: live, can reach {n} on Beta or release ({len(live)} total; "
                f"{len(rel)} recent or new feature rollouts listed; {len(msg)} recent messaging-only "
                f"rollouts not listed; {len(new)} require exactly {n})", "",
                f"Listed: minimum version {n - 1} or later, not started yet, or started since "
                f"{nim['recent_from']}. The rest, older long-running rollouts, are in signals.json. "
                "Channel comes from the targeting expression; `(any)` means none was found there.",
                ""]
        out += table(["Slug", "App", "Type", "Channel", "Min version", "Features", "Start"],
                     [(e["slug"], e["app"], "rollout" if e["rollout"] else "experiment",
                       e["channel"] or "(any)", e["min_version"] or "--",
                       ", ".join(e["features"][:3]), e["start"] or "not started")
                      for e in rel]) + [""]
    perf = sig.get("perf", {})
    if "summaries" in perf:
        s = perf["summaries"]
        open_regs = [x for x in s if x["regressions"] and x["status"] in PERF_OPEN]
        out += [f"## Perfherder alert summaries pushed during the cycle ({len(s)}; "
                f"{len(open_regs)} with regressions still open)", ""]
        st = collections.Counter(x["status"] for x in s)
        out += ["Statuses: " + ", ".join(f"{k}: {v}" for k, v in st.most_common()) + ".", ""]
        rows = [x for x in s if x["regressions"] and x["status"] not in ("invalid", "backedout")]
        out += table(["Alert", "Status", "Regr/impr", "Worst", "Bug", "Cycle bug", "Top tests"],
                     [(x["id"], x["status"], f"{x['regressions']}/{x['improvements']}",
                       f"{x['max_regression_pct']:.1f}%", x["bug"] or "--",
                       ", ".join(x["cycle_bugs"]) or "--", "; ".join(x["top"]))
                      for x in rows[:40]]) + [""]
        imps = sorted([x for x in s if x["improvements"] and not x["regressions"]],
                      key=lambda x: -x["max_improvement_pct"])
        out += [f"### Improvement-only summaries ({len(imps)}, top 15)", ""]
        out += table(["Alert", "Improvements", "Best", "Top tests"],
                     [(x["id"], x["improvements"], f"{x['max_improvement_pct']:.1f}%",
                       "; ".join(x["top"])) for x in imps[:15]]) + [""]
    cr = sig.get("crashes", {})
    if "candidates" in cr:
        t = cr["totals"]
        out += [f"## Nightly crash signatures: new or rising vs the previous cycle "
                f"({len(cr['candidates'])})", "",
                f"Builds and crash dates within each cycle window. Totals: {t['current']} crashes "
                f"now vs {t['previous']} before. `installs` is distinct install times, the closer "
                f"measure of how many users are hit. New means no crash in the previous window.", ""]
        def bug_list(bugs):
            shown = sorted(bugs, key=lambda b: -int(b["id"]))[:4]
            text = ", ".join(f"{b['id']} ({b['status'] or 'restricted'}"
                             f"{', from ' + '/'.join(b['regressed_by_cycle_bug']) if b['regressed_by_cycle_bug'] else ''})"
                             for b in shown)
            return (text + (f" (+{len(bugs) - 4} older)" if len(bugs) > 4 else "")) or "none filed"
        out += ["`Last build` is the newest Nightly build with this crash, including the next "
                "cycle's Nightlies. `Active` means that build is in the last fifth of this cycle or "
                "later, so the crash was not fixed before the merge.", ""]
        out += table(["Kind", "Signature", "Crashes", "Installs", "Prev installs", "Last build",
                      "Active", "Bugs"],
                     [(c["kind"], f"`{c['signature'][:90]}`", c["count"], c["installs"],
                       c["prev_installs"], c.get("last_build") or "--",
                       "yes" if c.get("active_at_end") else "no", bug_list(c.get("bugs", [])))
                      for c in cr["candidates"]]) + [""]
    cp = sig.get("crash_pings", {})
    if "candidates" in cp:
        t = cp["totals"]
        out += [f"## Crash pings (opt-out telemetry): new or rising on Nightly {n} "
                f"({len(cp['candidates'])})", "",
                f"Daily samples from crash-pings.mozilla.org, {n}.0a1 over this cycle vs "
                f"{n - 1}.0a1 over the previous one ({t['current']} vs {t['previous']} pings). Shares "
                f"are compared, not counts, because sample sizes differ. At least {PING_MIN_COUNT} "
                "pings; rising means 3x the share.", ""]
        out += table(["Kind", "Signature", "Pings", "Share", "Prev share"],
                     [(c["kind"], f"`{c['signature'][:90]}`", c["count"], f"{c['share_pct']}%",
                       f"{c['prev_share_pct']}%") for c in cp["candidates"][:30]]) + [""]
        out += ["### Top 15 by pings", ""]
        out += table(["Signature", "Pings", "Share", "Prev share"],
                     [(f"`{c['signature'][:90]}`", c["count"], f"{c['share_pct']}%",
                       f"{c['prev_share_pct']}%") for c in cp["top"]]) + [""]
    im = sig.get("intermittents", {})
    if "candidates" in im:
        out += [f"## Intermittent failures: new or 3x more frequent on trunk "
                f"({len(im['candidates'])})", "",
                f"Treeherder failure classifications per bug, this cycle vs the previous one, at "
                f"least {INTERMITTENT_MIN} failures. Bugs regressed by a cycle bug come first.", ""]
        out += table(["Bug", "Failures", "Prev", "Filed in cycle", "From cycle bug", "Component",
                      "Status", "Summary"],
                     [(c["bug"], c["count"], c["prev_count"], "yes" if c["filed_in_cycle"] else "",
                       ", ".join(c["regressed_by_cycle_bug"]), c["component"] or "(restricted)",
                       c["status"] or "--", (c["summary"] or "")[:70])
                      for c in im["candidates"][:40]]) + [""]
    td = sig.get("test_disables", {})
    if "areas" in td:
        out += [f"## Test manifest conditions changed during the cycle ({td['total_added']} lines "
                f"added, {td['total_removed']} removed)", "",
                "Condition lines (skip-if, fail-if and similar) in test manifests, diffed between "
                "the cycle tags and grouped by directory. A positive net means tests were disabled "
                "or marked failing. Path and keyword heuristics.", ""]
        out += table(["Area", "Net", "Added", "Removed", "Manifests", "Bugs cited"],
                     [(r["area"], r["net"], r["added"], r["removed"], r["files"],
                       ", ".join(r["bugs"])) for r in td["areas"][:25] if r["net"] > 0]) + [""]
    wc = sig.get("webcompat", {})
    if "bugzilla" in wc:
        rows = wc["bugzilla"]
        out += [f"## Webcompat ({len(rows)} webcompat-keyword bugs filed during the cycle; "
                f"{len(wc['flagged'])} point at {n})", "",
                f"Platform bugs: {sum(r['platform_bug'] for r in rows)}; site reports: "
                f"{sum(r['site_report'] for r in rows)}. Diagnosis teams: "
                + (", ".join(f"{k} {v}" for k, v in wc["diagnosis_teams"].items()) or "none") + ".",
                "",
                f"Flagged: regressed by a cycle bug, reproduced by the autowebcompat bot on Nightly "
                f"but not on stable, or marked affected in {n} and unaffected in {n - 1}.", ""]
        out += table(["Bug", "Why", "Component", "Status", "Repro channels", "Impact", "Summary"],
                     [(r["id"], ", ".join(w for w, ok in (
                         (f"from {'/'.join(r['from_cycle_bug'])}", r["from_cycle_bug"]),
                         ("nightly-only repro", r["nightly_only_repro"]),
                         (f"new in {n}", r["new_in_n"])) if ok),
                       r["component"], r["status"], r["repro_channels"] or "--",
                       r["impact"] or "--", r["summary"][:70]) for r in wc["flagged"]]) + [""]
        reps = wc["webcompat_com"]
        out += [f"### webcompat.com reports from Firefox {n} users ({len(reps)})", ""]
        if wc.get("webcompat_com_error"):
            out += [f"Not collected: {wc['webcompat_com_error']}", ""]
        out += ["`Other reports` counts reports for the same domain from other Firefox versions "
                "in the same window: 0 suggests the problem is specific to this version.", ""]
        out += table(["Issue", "Domain", "Problem", "OS", "Channel", "Other browser", "Other reports"],
                     [(f"[{r['number']}]({r['html_url']})", r["domain"], r["problem"] or "--",
                       r["os"] or "--", r["channel"] or "--", r["other_browser"] or "--",
                       r["other_version_reports"] if r["other_version_reports"] is not None else "?")
                      for r in reps]) + [""]
    oi = sig.get("open_issues", {})
    if "bugs" in oi:
        bugs = oi["bugs"]
        public = [b for b in bugs if not b["security"]]
        sec = [b for b in bugs if b["security"]]
        out += [f"## Open defects filed during the cycle by users ({len(bugs)})", "",
                f"Products: {', '.join(oi['products'])}. Reporter rule: {oi['reporter_rule']}. "
                f"Dropped after the query: "
                + (", ".join(f"{k} {v}" for k, v in oi["dropped"].items()) or "none")
                + ". Security bugs are counted here and listed in signals.json only.", "",
                f"Unconfirmed: {sum(b['status'] == 'UNCONFIRMED' for b in bugs)}; regressions: "
                f"{sum(b['regression'] for b in bugs)} ({sum(b['regression'] and not b['regressor_known'] for b in bugs)} "
                f"with no known regressor, {sum(bool(b['regressed_by_cycle_bug']) for b in bugs)} from a "
                f"cycle bug); S1/S2: {sum(b['severity'] in ('S1', 'S2') for b in bugs)}; crashes: "
                f"{sum(b['crash'] for b in bugs)}; affected {n}: "
                f"{sum(b['status_n'] == 'affected' for b in bugs)}.", ""]
        bulk = oi.get("bulk_reporters", [])
        out += [f"Bulk filers ({BULK_REPORTER_MIN}+ bugs each in the window, often code audits or "
                f"tool output rather than users hitting a problem): {len(bulk)} reporters, "
                f"{sum(r['bugs'] for r in bulk)} bugs. Main areas: "
                + "; ".join(", ".join(f"{c} {k}" for c, k in r["components"].items()) for r in bulk[:4])
                + ". They are left out of the component ranking, and listed below only when they "
                "are regressions, S1/S2 or crashes.", ""]
        flagged = [b for b in public if b["regression"] or b["severity"] in ("S1", "S2")
                   or b["crash"] or (b["new_in_n"] and not b["bulk_reporter"])]
        flagged.sort(key=lambda b: (not b["regressed_by_cycle_bug"], b["regressor_known"],
                                    b["severity"] or "S9"))
        out += [f"### Regressions, S1/S2, crashes and new-in-{n} ({len(flagged)}, non-security)", ""]
        out += table(["Bug", "Why", "Severity", "Status", f"Status {n}", "Component", "Summary"],
                     [(b["id"], ", ".join(w for w, ok in (
                         (f"regression from {'/'.join(b['regressed_by_cycle_bug'])}",
                          b["regressed_by_cycle_bug"]),
                         ("regression, cause unknown", b["regression"] and not b["regressor_known"]),
                         ("older regression", b["regression"] and b["regressor_known"]
                          and not b["regressed_by_cycle_bug"]),
                         ("crash", b["crash"]), (f"new in {n}", b["new_in_n"])) if ok) or "severity",
                       b["severity"] or "--", b["status"], b["status_n"] or "---", b["component"],
                       b["summary"][:70]) for b in flagged]) + [""]
        comps = collections.Counter(b["component"] for b in public if not b["bulk_reporter"])
        out += ["### Where users reported most (non-security, bulk filers excluded, top 12)", ""]
        out += table(["Component", "Bugs", "Unconfirmed"],
                     [(c, k, sum(b["status"] == "UNCONFIRMED" for b in public
                                 if b["component"] == c and not b["bulk_reporter"]))
                      for c, k in comps.most_common(12)]) + [""]
        ratings = collections.Counter(b["rating"] or "unrated" for b in sec)
        sec_comps = collections.Counter(b["component"] for b in sec)
        out += [f"### Security reports from users ({len(sec)}; details in signals.json)", "",
                "Ratings: " + ", ".join(f"{k} {v}" for k, v in ratings.most_common()) + ".", "",
                "Top areas: " + ", ".join(f"{c} {k}" for c, k in sec_comps.most_common(8)) + ".", ""]
    for k, v in sig.items():
        if isinstance(v, dict) and "skipped" in v:
            out += [f"**{k} skipped:** {v['skipped']}.", ""]
        if isinstance(v, dict) and "failed" in v:
            out += [f"**{k} not collected:** {v['failed']}", ""]
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--version", type=int, required=True)
    p.add_argument("--repo", help="Gecko checkout (default: as saved by watchlist.py check-setup)")
    p.add_argument("--relman", default=str(setup_check.RELMAN_ROOT),
                   help="Relman checkout providing pref-delta.py (default: this one)")
    p.add_argument("--outdir", help="default: same as cycle-data.py")
    p.add_argument("--skip", action="append", default=[],
                   choices=["prefs", "nimbus", "perf", "crashes", "crash_pings", "intermittents",
                            "test_disables", "webcompat", "open_issues"])
    args = p.parse_args()
    args.repo = str(setup_check.resolve_repo(args.repo))
    outdir = Path(args.outdir).expanduser() if args.outdir else setup_check.default_outdir(args.version)
    global KEY
    KEY, problem = setup_check.bugzilla_status()
    setup_check.report_bugzilla(problem)
    tools = setup_check.missing_tools(args.relman)
    for what, fix in tools:
        print(f"# NOTE: {what} will not be collected: {fix}", file=sys.stderr)
    cycle = json.loads((outdir / "cycle.json").read_text())
    previous = outdir / "signals.json"
    sig = json.loads(previous.read_text()) if args.skip and previous.is_file() else {}
    for name, fn in (("prefs", collect_prefs), ("nimbus", collect_nimbus), ("perf", collect_perf),
                     ("crashes", collect_crashes), ("crash_pings", collect_crash_pings),
                     ("intermittents", collect_intermittents),
                     ("test_disables", collect_test_disables), ("webcompat", collect_webcompat),
                     ("open_issues", collect_open_issues)):
        if name in args.skip:
            continue
        print(f"# collecting {name}", file=sys.stderr)
        try:
            sig[name] = fn(args, cycle)
        except Exception as e:
            sig[name] = {"failed": str(e)}
    sig["_setup"] = {"auth_problem": problem, "missing_tools": tools}
    (outdir / "signals.json").write_text(json.dumps(sig, indent=1))
    (outdir / "signals.md").write_text(render(sig, args.version))
    print(f"wrote {outdir / 'signals.md'} and {outdir / 'signals.json'}")


if __name__ == "__main__":
    main()
