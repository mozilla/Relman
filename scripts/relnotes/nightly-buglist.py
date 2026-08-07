#!/usr/bin/env python3
"""Pull the bug list for a Nightly build (or a whole day) from whattrainisitnow.com.

This is the list Release Management actually works from when hunting release notes, so it is the
right shared basis for comparing a scan against a human pass.

Two things to know about it:

1. **It does not filter backouts.** Every bug with a patch in the build is listed, including work
   that was backed out and never re-landed. Feed the output through scan-window.py --bugs-file to
   apply the Bugzilla-FIXED filter and the mechanical-noise drops.

2. **Build lists cannot be reproduced with a date-based git window.** Bugs are attributed to the
   build that shipped them, but a commit merged from autoland keeps its *original* committer date in
   the git mirror -- so `git log --since=...` will both miss bugs the build contains and include
   bugs it doesn't. Use this script (or a revision range) rather than a date filter when you need to
   match a build exactly.

Nightly builds 2-3 times a day at irregular times, so a day usually has several lists.

The bug ids are not in the page body -- they are URL-encoded inside the "Patches from N bugs" link
that points at a Bugzilla buglist. Note the page's collapsible "Show list of bugs" reveals a
*different* table ("Outstanding bugs": open bugs ranked by an impact score), which is not a list of
landings. This script reads the changelog link, not that table.

Usage:
  nightly-buglist.py                      # today, union across builds
  nightly-buglist.py --date 20260730      # a past day
  nightly-buglist.py --date 20260730 --per-build
  nightly-buglist.py --date 20260730 -o /tmp/bugs.txt
"""

import argparse
import re
import sys
import urllib.error as url_error
import urllib.parse as url_parse
import urllib.request as url_request
from pathlib import Path

BASE = "https://whattrainisitnow.com/nightly/"
USER_AGENT = "Relman-relnotes-nightly-buglist/1.0"

BUILD_ID_RE = re.compile(r"(\d{14})")
PATCHES_LINK_RE = re.compile(r"href=\"([^\"]*?)\"[^>]*>\s*Patches from (\d+) bugs")
BUG_ID_RE = re.compile(r"\b\d{6,14}\b")


def fetch(url: str) -> str:
    req = url_request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with url_request.urlopen(req) as r:
            return r.read().decode("utf-8", "replace")
    except url_error.HTTPError as e:
        sys.exit(f"error: {url} returned HTTP {e.code} {e.reason}")
    except url_error.URLError as e:
        sys.exit(f"error: could not reach {url}: {e.reason}")


def parse_builds(html: str) -> dict[str, list[str]]:
    """{build_id: [bug ids]} for every build panel on the page.

    Each "Patches from N bugs" link is paired with the nearest preceding 14-digit build
    id in document order, which is how the page nests its panels.
    """
    out: dict[str, list[str]] = {}
    for m in PATCHES_LINK_RE.finditer(html):
        preceding = BUILD_ID_RE.findall(html[: m.start()])
        build = preceding[-1] if preceding else "unknown"
        # The href is double-encoded in places (%252C as well as %2C).
        url = url_parse.unquote(url_parse.unquote(m.group(1)))
        # Build ids are exactly 14 digits (YYYYMMDDhhmmss); bug ids are 6-8. The previous
        # filter keyed on a hardcoded "2026" prefix, which would silently start dropping
        # real bug ids once Bugzilla reaches 8-digit numbers beginning with that year.
        ids = sorted({i for i in BUG_ID_RE.findall(url) if len(i) <= 8})
        claimed = int(m.group(2))
        if len(ids) != claimed:
            print(
                f"# warning: build {build} claims {claimed} bugs but {len(ids)} parsed",
                file=sys.stderr,
            )
        out.setdefault(build, [])
        out[build] = sorted(set(out[build]) | set(ids))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Bug list for a Nightly build or day.")
    p.add_argument("--date", default=None, help="YYYYMMDD (default: today's page)")
    p.add_argument("--build", default=None, help="only this 14-digit build id")
    p.add_argument("--per-build", action="store_true", help="group output by build")
    p.add_argument("-o", "--output", default=None, help="write ids here, one per line")
    args = p.parse_args()

    url = f"{BASE}?date={args.date}" if args.date else BASE
    html = fetch(url)
    builds = parse_builds(html)
    if not builds:
        sys.exit(f"error: no build panels found at {url} (page layout may have changed)")

    if args.build:
        if args.build not in builds:
            sys.exit(
                f"error: build {args.build} not on this page. Available: "
                f"{', '.join(sorted(builds))}"
            )
        builds = {args.build: builds[args.build]}

    union = sorted({b for ids in builds.values() for b in ids})
    total = sum(len(v) for v in builds.values())

    for build in sorted(builds):
        print(f"# build {build}: {len(builds[build])} bugs", file=sys.stderr)
    print(
        f"# {len(builds)} build(s), {total} listed, {len(union)} distinct "
        f"({total - len(union)} appear in more than one build)",
        file=sys.stderr,
    )
    print("# NOTE: backouts are NOT filtered out of this list", file=sys.stderr)

    if args.per_build:
        lines = []
        for build in sorted(builds):
            lines.append(f"# {build}")
            lines.extend(builds[build])
        body = "\n".join(lines)
    else:
        body = "\n".join(union)

    if args.output:
        Path(args.output).write_text(body + "\n")
        print(f"# wrote {args.output}", file=sys.stderr)
    else:
        print(body)


if __name__ == "__main__":
    main()
