#!/usr/bin/env bash
# Fetch the two triage sources for the triage-user-bugs skill via the Bugzilla
# REST API (buglist.cgi is WAF-challenged; REST is not).
#
# Source 1: community-filed bugs in the last COMMUNITY_WINDOW (default 24h),
#           approximated with the UNCONFIRMED-status proxy for the
#           %group.editbugs% / %group.mozilla-corporation% exclusion that the
#           REST API cannot evaluate.
# Source 2: bugs that got the regressionwindow-wanted keyword in the last
#           REGWINDOW (default 4d).
#
# Read-only. Writes JSON to OUTDIR and prints a deduped metadata table.
#
# Usage: fetch-bugs.sh [COMMUNITY_WINDOW] [REGWINDOW] [OUTDIR]
#   COMMUNITY_WINDOW  Bugzilla relative time, default -24h
#   REGWINDOW         Bugzilla relative time, default -4d
#   OUTDIR            output dir, default a mktemp dir

set -euo pipefail

COMMUNITY_WINDOW="${1:--24h}"
REGWINDOW="${2:--4d}"
OUTDIR="${3:-$(mktemp -d)}"
mkdir -p "$OUTDIR"

BZ="https://bugzilla.mozilla.org/rest/bug"
FIELDS="include_fields=id,summary,product,component,op_sys,version,status,creation_time,keywords,regressed_by,cf_status_firefox_esr"

# Full relman product set (matches the advanced-search query).
PRODUCTS=(
  "Core" "DevTools" "External Software Affecting Firefox" "Fenix" "Firefox"
  "Firefox Build System" "Firefox for Android" "Firefox for Echo Show"
  "Firefox for FireTV" "Firefox for iOS" "Focus" "Focus-iOS" "Geckoview"
  "NSPR" "NSS" "Toolkit" "WebExtensions"
)
prod_qs=""
for p in "${PRODUCTS[@]}"; do
  prod_qs+="&product=$(printf '%s' "$p" | sed 's/ /%20/g')"
done

CLASS="classification=Client%20Software&classification=Developer%20Infrastructure&classification=Components"

echo "Query window: community=${COMMUNITY_WINDOW}  regressionwindow-wanted=${REGWINDOW}" >&2
echo "Output dir:   ${OUTDIR}" >&2

# Source 1 — community UNCONFIRMED, created in window, not the intermittent bug filer.
curl -s "${BZ}?chfield=%5BBug%20creation%5D&chfieldfrom=${COMMUNITY_WINDOW}&chfieldto=Now&${CLASS}${prod_qs}&resolution=---&bug_status=UNCONFIRMED&f1=reporter&o1=notequals&v1=intermittent-bug-filer@mozilla.bugs&${FIELDS}" \
  -o "${OUTDIR}/source1_community.json"

# Source 2 — regressionwindow-wanted keyword added in window.
curl -s "${BZ}?chfield=keywords&chfieldvalue=regressionwindow-wanted&chfieldfrom=${REGWINDOW}&chfieldto=Now&f1=keywords&o1=substring&v1=regressionwindow-wanted&resolution=---&${FIELDS}" \
  -o "${OUTDIR}/source2_regwindow.json"

python3 - "$OUTDIR" <<'PY'
import json, sys, os
outdir = sys.argv[1]
def load(fn):
    try:
        return json.load(open(os.path.join(outdir, fn))).get("bugs", [])
    except Exception as e:
        print(f"ERROR loading {fn}: {e}", file=sys.stderr); return []

s1 = load("source1_community.json")
s2 = load("source2_regwindow.json")
src = {}
for b in s1: src.setdefault(b["id"], b)["_src"] = "community"
for b in s2:
    b2 = src.setdefault(b["id"], b)
    b2["_src"] = "both" if b["id"] in {x["id"] for x in s1} else "regwindow"

bugs = sorted(src.values(), key=lambda b: (b.get("product",""), b.get("component","")))
print(f"\nSource 1 (community/UNCONFIRMED): {len(s1)}   Source 2 (regressionwindow-wanted): {len(s2)}   unique: {len(bugs)}\n")
for b in bugs:
    kw = ",".join(b.get("keywords", [])) or "-"
    rb = b.get("regressed_by") or []
    print(f"{b['id']} [{b['_src']}] {b.get('product','')}::{b.get('component','')} | {b.get('op_sys','')} | v{b.get('version','')} | kw:{kw} | regressed_by:{rb}")
    print(f"    {b.get('summary','')}")
print(f"\nJSON: {outdir}/source1_community.json , {outdir}/source2_regwindow.json")
print("NOTE: community list uses the UNCONFIRMED-status proxy for the editbugs/mozilla-corporation group exclusion.")
PY
