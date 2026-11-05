#!/usr/bin/env python3
"""List live Nimbus experiments and rollouts that touch a feature or a preference.

Tree defaults (pref-delta.py) say what a build ships with; a live Nimbus enrollment can override
them for part of a channel. This asks Experimenter which live enrollments do.

  nimbus-live.py --feature doh,listen-to-page              # Fenix features (nimbus.fml.yaml)
  nimbus-live.py --feature urlbar --app firefox-desktop    # desktop features
  nimbus-live.py --pref browser.urlbar.unifiedSearchButton.always

--pref searches desktop enrollments two ways: a feature variable that FeatureManifest.yaml maps to
the preference with setPref or fallbackPref, and any branch value naming the preference (prefFlips and similar).
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

EXPERIMENTER = "https://experimenter.services.mozilla.com/api/v6/experiments/"
MANIFEST = "toolkit/components/nimbus/FeatureManifest.yaml"


def live(app):
    return json.loads(trainlib.fetch_text(f"{EXPERIMENTER}?application={app}&status=Live"))


def mapped_variables(repo, ref, prefs):
    """{pref: {(feature, variable), ...}} for variables mapped to it by setPref or fallbackPref."""
    text = subprocess.run(["git", "-C", repo, "show", f"{ref}:{MANIFEST}"],
                          capture_output=True, text=True, check=True).stdout
    found, feature, variable = {}, None, None
    for line in text.splitlines():
        if m := re.match(r"^([A-Za-z0-9_-]+):\s*$", line):
            feature = m.group(1)
        elif m := re.match(r"^    ([A-Za-z0-9_-]+):\s*$", line):
            variable = m.group(1)
        for p in prefs:
            # setPref writes the pref; fallbackPref leaves it alone but an enrollment
            # overrides what getVariable() returns, so both put the gate under Nimbus.
            if re.search(r'\b(pref|fallbackPref):\s*"?%s"?\s*$' % re.escape(p), line):
                found.setdefault(p, set()).add((feature, variable))
    return found


def describe(e, values):
    targeting = e.get("targeting") or ""
    named = re.findall(r'update\.channel\s*==\s*"(\w+)"', targeting)
    for group in re.findall(r'update\.channel\s+in\s+\[([^\]]*)\]', targeting):
        named += re.findall(r'"(\w+)"', group)
    channel = e.get("channel") or ",".join(sorted(set(named))) or "any"
    bucket = e.get("bucketConfig") or {}
    share = f"{bucket['count']}/{bucket['total']}" if bucket.get("total") else "?"
    print(f"  {e['slug']}  {'rollout' if e.get('isRollout') else 'experiment'}  channel={channel}  "
          f"population={share}{'  (enrollment paused)' if e.get('isEnrollmentPaused') else ''}")
    print(f"    targeting: {(e.get('targeting') or '')[:200]}")
    for branch, value in values:
        print(f"    {branch}: {json.dumps(value)[:200]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--feature", help="comma-separated Nimbus feature ids")
    ap.add_argument("--pref", help="comma-separated preference names (desktop)")
    ap.add_argument("--app", default="fenix", help="Experimenter application for --feature "
                    "(fenix, firefox-desktop, firefox-ios, focus-android)")
    ap.add_argument("--repo", help="Gecko checkout (default: saved by watchlist.py check-setup)")
    ap.add_argument("--rev", default="origin/main", help="revision to read FeatureManifest.yaml from")
    args = ap.parse_args()
    if not args.feature and not args.pref:
        ap.error("give --feature and/or --pref")

    for feature in filter(None, (args.feature or "").split(",")):
        print(f"feature {feature} ({args.app}):")
        hits = [e for e in live(args.app) if feature in e.get("featureIds", [])]
        for e in hits:
            describe(e, [(b["slug"], f["value"]) for b in e.get("branches", [])
                         for f in b.get("features", []) if f["featureId"] == feature])
        if not hits:
            print("  no live experiment or rollout")

    prefs = [p for p in (args.pref or "").split(",") if p]
    if prefs:
        mapped = mapped_variables(trainlib.resolve_repo(args.repo), args.rev, prefs)
        desktop = live("firefox-desktop")
        for p in prefs:
            variables = mapped.get(p, set())
            names = ", ".join(sorted(f"{f}.{v}" for f, v in variables)) or "none"
            print(f"pref {p} (firefox-desktop; Nimbus variables: {names}):")

            def sets_gate(f):
                # The enrollment controls the gate only if it sets the mapped variable,
                # or names the pref itself (prefFlips and similar).
                value = f.get("value") or {}
                return (any((f["featureId"], v) in variables for v in value)
                        or p in json.dumps(value))

            hits = []
            for e in desktop:
                values = [(b["slug"], f["value"]) for b in e.get("branches", [])
                          for f in b.get("features", []) if sets_gate(f)]
                if values:
                    hits.append(e)
                    describe(e, values)
            if not hits:
                print("  no live experiment or rollout")


if __name__ == "__main__":
    main()
