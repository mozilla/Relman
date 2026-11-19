#!/usr/bin/env python3
"""Fetch Firefox Desktop and Android shipped versions and print them in the terminal.

chmod +x this file and set it as a bash alias:
    alias v="path/to/firefox_versions.py"

The output looks like this (colored in a terminal, plain when piped
or when NO_COLOR is set):

 Firefox Desktop
   ESR 115       115.45.0esr
   ESR 153       153.7.0esr
   Release       160.0.1       ━━━━━━━━━━ 100%  build1
   Beta          161.0b5       ━━━━━━━━━━ 100%  build1
   DevEdition    161.0b5       ━━━━━━━━━━ 100%  build1
   Nightly       162.0a1       ● updates on

 Firefox Android
   Release       160.0.1
   Beta          161.0b5
   Nightly       162.0a1

 Schedule
   Next merge    2026-11-19  (tomorrow)  162 → beta
   Next release  2026-11-24  (in 6 days)  Firefox 161

Dates are relative to the local date of the machine running the script.
Only the Python standard library is used.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

AUS = "https://aus-api.mozilla.org/api/v1/"
PD = "https://product-details.mozilla.org/1.0/"
WTN = "https://whattrainisitnow.com/api/"
ENDPOINTS = {
    "desktop": PD + "firefox_versions.json",
    "android": PD + "mobile_versions.json",
    "release_rollout": AUS + "rules/firefox-release",
    "beta_rollout": AUS + "rules/firefox-beta",
    "deved_rollout": AUS + "rules/devedition",
    "nightly_status": AUS + "emergency_shutoff/Firefox/nightly",
    "schedule_release": WTN + "release/schedule/?version=release",
    "schedule_beta": WTN + "release/schedule/?version=beta",
    "schedule_nightly": WTN + "release/schedule/?version=nightly",
}

# Colors, disabled when piping the output or when NO_COLOR is set
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(code, s):
    return f"\033[{code}m{s}\033[0m" if USE_COLOR else s


def bold(s):
    return c("1", s)


def dim(s):
    return c("2", s)


def red(s):
    return c("1;31", s)


def green(s):
    return c("1;32", s)


def yellow(s):
    return c("1;33", s)


def cyan(s):
    return c("96", s)


def fetch_json(url):
    """Return the decoded JSON body, or None. Error responses (e.g. a 404) are
    decoded too, since some endpoints answer with a JSON body."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def fetch_all(endpoints):
    with ThreadPoolExecutor(max_workers=len(endpoints)) as pool:
        futures = {key: pool.submit(fetch_json, url) for key, url in endpoints.items()}
    return {key: f.result() for key, f in futures.items()}


def as_dict(value):
    return value if isinstance(value, dict) else {}


def leading_int(s):
    """Leading integer of a string, e.g. "115.45.0esr" => 115, 0 if none."""
    m = re.match(r"\s*(\d+)", str(s or ""))
    return int(m.group(1)) if m else 0


def extract_bug_numbers(text):
    """Unique bug numbers mentioned as "bug 123456", in order of appearance."""
    return list(dict.fromkeys(re.findall(r"bug\s+(\d+)", text, re.IGNORECASE)))


def pad(s, width):
    """ljust() that ignores ANSI escape codes."""
    visible = len(re.sub(r"\033\[[0-9;]*m", "", s))
    return s + " " * max(0, width - visible)


def section(title):
    print()
    print(" " + bold(green(title)))


def row(label, version, extra=""):
    version = bold(version) if version else dim("n/a")
    print("   " + pad(cyan(label), 14) + (pad(version, 14) + extra if extra else version))


def rollout(rule, shipped):
    """Rollout percentage as a small progress bar, plus the build mapped in Balrog."""
    if not isinstance(rule, dict) or "backgroundRate" not in rule:
        return red("rollout unavailable")
    rate = int(rule["backgroundRate"] or 0)
    filled = int(round(rate / 10))
    color = green if rate >= 100 else (yellow if rate > 0 else red)
    bar = color("━" * filled) + dim("─" * (10 - filled))

    # "Firefox-160.0.1-build1" => ("160.0.1", "build1")
    mapping = rule.get("mapping") or ""
    m = re.search(r"-([^-]+)-(build\d+)$", mapping)
    build = (("" if m.group(1) == shipped else m.group(1) + " ") + m.group(2)) if m else mapping

    return bar + " " + color(f"{rate}%".rjust(4)) + "  " + dim(build)


def when(day):
    """"2026-11-19" => "2026-11-19  (tomorrow)", relative to the local date."""
    if not day:
        return dim("n/a")
    days = (date.fromisoformat(day) - date.today()).days
    if days == 0:
        rel = "today"
    elif days == 1:
        rel = "tomorrow"
    elif days == -1:
        rel = "yesterday"
    elif days > 1:
        rel = f"in {days} days"
    else:
        rel = f"{abs(days)} days ago"
    return bold(day) + "  " + (yellow(f"({rel})") if 0 <= days <= 1 else dim(f"({rel})"))


def next_milestone(response, milestone):
    """Date of the next upcoming milestone from the whattrainisitnow schedules,
    which are more reliable than product-details for future dates. We look at
    all the channels and keep the earliest date from today on, so it stays
    correct around merge and release days when channels have just moved.
    Version numbers are not taken from here, they come from product-details."""
    today = date.today().isoformat()
    upcoming = []
    for key in ("schedule_release", "schedule_beta", "schedule_nightly"):
        day = str(as_dict(response.get(key)).get(milestone) or "")[:10]
        if day >= today:
            upcoming.append(day)
    return min(upcoming, default="")


def main():
    response = fetch_all(ENDPOINTS)

    desktop = as_dict(response["desktop"])
    android = as_dict(response["android"])
    # The shutoff endpoint answers with a JSON 404 (no "comment") when updates are on
    nightly_known = isinstance(response["nightly_status"], dict)
    nightly_message = as_dict(response["nightly_status"]).get("comment")
    nightly_updates_on = nightly_known and nightly_message is None

    section("Firefox Desktop")
    # ESR labels are derived from the version numbers so they never go stale
    for key in ("FIREFOX_ESR115", "FIREFOX_ESR", "FIREFOX_ESR_NEXT"):
        if desktop.get(key):
            row(f"ESR {leading_int(desktop[key])}", desktop[key])
    release = desktop.get("LATEST_FIREFOX_VERSION") or ""
    beta = desktop.get("LATEST_FIREFOX_RELEASED_DEVEL_VERSION") or ""
    deved = desktop.get("FIREFOX_DEVEDITION") or ""
    row("Release", release, rollout(response["release_rollout"], release))
    row("Beta", beta, rollout(response["beta_rollout"], beta))
    row("DevEdition", deved, rollout(response["deved_rollout"], deved))
    row("Nightly", desktop.get("FIREFOX_NIGHTLY") or "",
        red("update status unavailable") if not nightly_known
        else green("●") + " updates on" if nightly_updates_on
        else red("● UPDATES STOPPED"))

    # Reason why Nightly updates are stopped, and the bugs it mentions
    if nightly_known and not nightly_updates_on:
        message = str(nightly_message)
        print("     " + yellow(message))
        bugs = extract_bug_numbers(message)
        if bugs:
            print("     " + red("https://bugzilla.mozilla.org/buglist.cgi?bug_id=" + "%2C".join(bugs)))

    section("Firefox Android")
    row("Release", android.get("version") or "")
    row("Beta", android.get("beta_version") or "")
    row("Nightly", android.get("nightly_version") or "")

    section("Schedule")
    # Versions from product-details: Nightly is the next to merge to beta,
    # the next release is the one after the current release.
    merge_version = leading_int(desktop.get("FIREFOX_NIGHTLY"))
    release_version = leading_int(desktop.get("LATEST_FIREFOX_VERSION"))
    release_version = release_version + 1 if release_version else 0
    row("Next merge", when(next_milestone(response, "merge_day"))
        + (dim(f"  {merge_version} → beta") if merge_version else ""))
    row("Next release", when(next_milestone(response, "release"))
        + (dim(f"  Firefox {release_version}") if release_version else ""))
    print()


if __name__ == "__main__":
    main()
