#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error as url_error
import urllib.parse as url_parse
import urllib.request as url_request
from pathlib import Path
from typing import Any

# Generates a list of new contributors to Desktop Firefox for a given version.

PRODUCTS = [
    "Core",
    "Developer Infrastructure",
    "DevTools",
    "Firefox",
    "Firefox Build System",
    "NSPR",
    "NSS",
    "Remote Protocol",
    "Testing",
    "Toolkit",
    "Web Compatibility",
    "WebExtensions",
]


class Error(Exception):
    """throwing this won't generate a stack trace"""


def plural(count: int, item: str, *, suffix: str = "s") -> str:
    return f"{count:,d} {item}{'' if count == 1 else suffix}"


def bmo_request(
    end_point: str,
    query: dict[str, Any],
    *,
    api_key: str | None = None,
) -> Any:
    # dict to query-string
    query_args = []
    for name, value in query.items():
        if isinstance(value, list):
            query_args.extend((name, v) for v in value)
        else:
            query_args.append((name, value))
    query_encoded = url_parse.urlencode(query_args)

    # build request
    req = url_request.Request(
        f"https://bugzilla.mozilla.org/rest/{end_point}?{query_encoded}",
        headers={
            "User-Agent": "new-contributors",
            "X-BUGZILLA-API-KEY": api_key if api_key else "",
        },
    )

    # return json response
    try:
        with url_request.urlopen(req) as r:
            return json.load(r)
    except url_error.HTTPError as e:
        try:
            res = json.load(e.fp)
            raise Error(res["message"])
        except (OSError, ValueError, KeyError):
            raise Error(e)


def main() -> None:
    # parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "version",
        type=int,
        help="Firefox version",
    )
    parser.add_argument(
        "--api-key",
        "--apikey",
        required=True,
        help="Bugzilla API-Key",
    )
    args = parser.parse_args()
    if args.version < 0:
        raise Error(f"Invalid version: {args.version}")

    # load cache
    # store a list of users against each version that were determined to have patches
    # landed _prior_ to the specified version
    cache_file = Path(__file__).parent / "new-contributors.cache"
    try:
        with cache_file.open() as f:
            cache = json.load(f)
    except (FileNotFoundError, ValueError):
        cache = []
    current_cache = None
    for cache_item in cache:
        if cache_item["version"] == args.version:
            current_cache = cache_item
            break
    if not current_cache:
        current_cache = {"version": args.version, "skip": []}
        cache.append(current_cache)

    # find bugs fixed in specified version
    print(f"looking for bugs fixed in Firefox {args.version}", file=sys.stderr)
    bugs = bmo_request(
        "bug",
        {
            "target_milestone": f"{args.version} Branch",
            "status": ["RESOLVED", "VERIFIED"],
            "product": PRODUCTS,
            "include_fields": "id,assigned_to,cf_last_resolved",
            "order": "cf_last_resolved",
        },
        api_key=args.api_key,
    )["bugs"]
    print(f"found {plural(len(bugs), 'bug')}", file=sys.stderr)
    if not bugs:
        return

    # find new assignees
    new = {}
    for bug in bugs:
        assignee = bug["assigned_to"]

        # skip users that are clearly employees or contractors
        if assignee.endswith(
            (
                "@getpocket.com",
                "@mozilla.com",
                "@mozilla.org",
                "@mozillafoundation.org",
                "@softvision.com",
                "@softvision.ro",
                "@softvisioninc.eu",
            )
        ):
            continue
        # skip known bots
        if assignee in ("wptsync@mozilla.bugs",):
            continue
        # skip users we already know are not new
        should_skip = False
        for cache_item in cache:
            if cache_item["version"] <= args.version and assignee in cache_item["skip"]:
                should_skip = True
                break
        if should_skip:
            continue
        # handle users that we know are new and fixed more than one bug
        if assignee in new:
            new[assignee]["bugs"].append(bug["id"])
            continue

        print(f"checking {assignee}", file=sys.stderr, end="", flush=True)

        # always exclude employees; this is quicker than a bug search, and not
        # all bugs have correct metadata
        users = bmo_request(
            "user",
            {"names": assignee},
            api_key=args.api_key,
        )["users"]
        is_employee = False
        if users:
            for group in users[0]["groups"]:
                if group["name"] == "mozilla-employee-confidential":
                    is_employee = True
                    break
        if is_employee:
            print(" employee", file=sys.stderr)
            current_cache["skip"].append(assignee)
            continue
        print(" contributor", file=sys.stderr, end="", flush=True)

        # ignore bugs in a broken state
        if not bug.get("cf_last_resolved"):
            print(f" bug-{bug['id']} in unexpected state", file=sys.stderr)
            continue

        # query for bugs fixed by this user before this one
        prior_bugs = bmo_request(
            "bug",
            {
                # resolved bugs in our products
                "product": PRODUCTS,
                "status": ["RESOLVED", "VERIFIED"],
                # assigned to our user
                "emailassigned_to1": "1",
                "emailtype1": "exact",
                "email1": assignee,
                # where the last resolved is older than this bug's
                "f1": "cf_last_resolved",
                "o1": "lessthan",
                "v1": bug["cf_last_resolved"].replace("T", " ").replace("Z", ""),
                # and a target_milestone is set (filter our duplicates, etc)
                "f2": "target_milestone",
                "o2": "notequals",
                "v2": "---",
                # don't need the full list or count, just need to know if there's any
                "limit": 1,
            },
            api_key=args.api_key,
        )["bugs"]
        if prior_bugs:
            print(" existing", file=sys.stderr)
            current_cache["skip"].append(assignee)
            continue

        # collate in `new` dict
        print(" new", file=sys.stderr)
        new.setdefault(
            assignee,
            {
                "name": bug["assigned_to_detail"]["real_name"]
                or bug["assigned_to_detail"]["nick"],
                "bugs": [],
            },
        )
        new[assignee]["bugs"].append(bug["id"])
    print(f"found {plural(len(new), 'new contributor')}", file=sys.stderr)

    # update cache
    with cache_file.open("w") as f:
        json.dump(cache, f, indent=2, sort_keys=True)

    # generate nucleus output
    print(
        f"With the release of Firefox {args.version}, we are pleased to welcome "
        "the developers who contributed their first code change to Firefox in "
        f"this release, {len(new)} of whom were brand new volunteers! Please "
        "join us in thanking each of these diligent and enthusiastic "
        "individuals, and take a look at their contributions:\n"
    )
    for user in sorted(new.values(), key=lambda u: u["name"].lower()):
        bug_links = ", ".join(
            f'<a href="https://bugzilla.mozilla.org/{b}">{b}</a>'
            for b in sorted(user["bugs"])
        )
        print(f"* {user['name']}: {bug_links}")


if __name__ == "__main__":
    try:
        main()
    except Error as error:
        print(error, file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(2)
