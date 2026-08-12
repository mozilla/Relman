#!/usr/bin/env python3
"""Query Bugzilla by the release-note flag, and cross-check a shipped note set against it.

`cf_tracking_firefox_relnote` is the field Release Management and developers use to mark a bug
for the notes. Every other script here goes the other way -- it finds bugs by what landed and
then reads their flag. This one inverts that: given a flag value, which bugs carry it?

That inversion is what makes two things possible:

1. **Coverage.** `--coverage 155.0a1` compares the published note set against the flagged bugs in
   both directions, so a flagged bug nobody wrote a note for, and a note whose bug was never
   flagged, both surface. Reviewing notes previously depended on the author supplying a bug list.

2. **The nomination queue.** `--nominated` lists bugs flagged `?`, i.e. someone proposed a note and
   nobody has decided yet. Defaults to bugs that are actually **fixed**: an open bug with `?` is a
   developer pre-registering an intention, which the team treats as noise rather than a decision
   waiting to be made. `--include-open` shows those too.

Flag values in use: `?` (nominated), `NNN+` (approved for that release), `nightly+` (Nightly-only
note), `-` (declined), `---` (unset).

The `?` value has to be percent-escaped as `%3F` and `+` as `%2B`, or Bugzilla sees a URL
delimiter and a query-string plus sign instead of the flag value. That is the whole reason this
takes a mode flag rather than a raw string.

**Security-restricted bugs are invisible here.** REST silently omits them rather than erroring, and
gives no count, so a coverage report can never prove a flagged bug does not exist -- only that none
is visible to you.

Usage:
  relnote-flag.py --nominated                  # the real decision queue (fixed + flagged ?)
  relnote-flag.py --nominated --include-open   # plus pre-registered intentions
  relnote-flag.py --approved 155               # bugs approved for 155's notes
  relnote-flag.py --nightly                    # Nightly-only notes
  relnote-flag.py --declined --limit 40        # what RelMan has said no to
  relnote-flag.py --coverage 155.0a1           # note set vs flagged bugs, both directions
  relnote-flag.py --coverage 153.0.3 --product "Firefox for Android"
  relnote-flag.py --coverage 153.0 --published-only    # only what has actually shipped
  relnote-flag.py --coverage 153.0.3 --scope release   # this release's notes alone
  relnote-flag.py --approved 155 --format json
"""

import argparse
import json
import re
import sys
import time
import urllib.parse as url_parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

BUGZILLA_REST = "https://bugzilla.mozilla.org/rest/bug"
NUCLEUS_NOTES = "https://nucleus.mozilla.org/rna/notes/?format=json"
NUCLEUS_RELEASES = "https://nucleus.mozilla.org/rna/releases/?format=json"
NUCLEUS_TTL = 3 * 3600

FIELDS = ("id,summary,product,component,status,resolution,"
          "cf_tracking_firefox_relnote,last_change_time")
FIXED_STATUS = {"RESOLVED", "VERIFIED"}


def cached_json(url: str, name: str, refresh: bool = False):
    """Nucleus responses are megabytes; don't refetch them for every invocation."""
    path = trainlib.CACHE_DIR / name
    if not refresh and path.exists() and (time.time() - path.stat().st_mtime) < NUCLEUS_TTL:
        try:
            return json.loads(path.read_text())
        except ValueError as e:
            # Refetching repairs it, so this is not fatal -- but say so, or a write that keeps
            # failing refetches megabytes on every run in silence.
            print(f"# warning: rewriting unreadable cache {path} ({e})", file=sys.stderr)
    try:
        data = trainlib.fetch_json(url)
    except RuntimeError as e:
        # Nucleus 502s and times out often enough that refusing to answer over it is worse than
        # answering from yesterday's copy. Only a cold cache is fatal.
        if not path.exists():
            raise
        age = (time.time() - path.stat().st_mtime) / 3600
        print(f"# WARNING: {e}\n#          falling back to the cached copy, {age:.1f}h old",
              file=sys.stderr)
        try:
            return json.loads(path.read_text())
        except ValueError:
            raise e from None  # the cache is unusable too; the outage is the real story
    trainlib.write_json_atomic(path, data, pretty=False)
    return data


def query(values: list[str], version: int | None = None) -> list[dict]:
    """Bugs whose relnote flag is any of `values`, with that release's status field."""
    fields = FIELDS + (f",cf_status_firefox{version}" if version else "")
    params = {
        "f1": "cf_tracking_firefox_relnote",
        "o1": "anyexact",
        "v1": ",".join(values),
        "include_fields": fields,
        "limit": "0",
    }
    url = f"{BUGZILLA_REST}?{url_parse.urlencode(params, quote_via=url_parse.quote)}"
    return trainlib.fetch_json(url).get("bugs", [])


def is_fixed(bug: dict) -> bool:
    return bug.get("status") in FIXED_STATUS and bug.get("resolution") == "FIXED"


def fetch_fields(bug_ids: list[int], fields: str) -> list[dict]:
    if not bug_ids:
        return []
    params = {"id": ",".join(str(i) for i in bug_ids), "include_fields": fields, "limit": "0"}
    url = f"{BUGZILLA_REST}?{url_parse.urlencode(params, quote_via=url_parse.quote)}"
    return trainlib.fetch_json(url).get("bugs", [])


def related_coverage(bug_ids: list[int], covered: set[int]) -> dict[int, tuple[int, str]]:
    """Which unmatched bugs are covered through a neighbour, and via which one.

    Two structural reasons a flagged bug legitimately has no note of its own, both seen in the 153
    cycle:

    - **Meta bug carries the note.** 2025322 ("Enable HDR video on Windows in release") is flagged,
      while the published note cites 2012848, the `[meta] HDR video on Windows` bug it blocks.
    - **Rollup note carries the note.** 2046952 ("Add URL bar to Smart Window new tab") is covered
      by a Smart Window rollup note that cites one sibling member, 2034643.

    One hop over `blocks` / `depends_on` catches the first directly and the second when the members
    share a parent. Anything still unmatched is worth a human look rather than an assumption.
    """
    if not bug_ids:
        return {}
    out: dict[int, tuple[int, str]] = {}
    neighbours = {b["id"]: (b.get("blocks") or []) + (b.get("depends_on") or [])
                  for b in fetch_fields(bug_ids, "id,blocks,depends_on")}

    pending = {}
    for bid, near in neighbours.items():
        hit = next((n for n in near if n in covered), None)
        if hit is not None:
            out[bid] = (hit, "blocks/depends_on")
        elif near:
            pending[bid] = near

    # Second hop, batched: one request for every candidate parent rather than one per bug. Every
    # neighbour is included -- capping the list would silently drop the parent that explains the
    # coverage and put a covered bug back in the gap list.
    if pending:
        wanted = sorted({n for near in pending.values() for n in near})
        parents = {p["id"]: (p.get("depends_on") or [])
                   for p in fetch_fields(wanted, "id,depends_on")}
        for bid, near in pending.items():
            for pid in near:
                sib = next((s for s in parents.get(pid, []) if s in covered), None)
                if sib is not None:
                    out[bid] = (sib, f"sibling via bug {pid}")
                    break
    return out


def is_published(release: dict) -> bool:
    return str(release.get("is_public")) == "True"


def nucleus_release(version: str, channel: str | None, refresh: bool,
                    product: str = "Firefox", published_only: bool = False) -> dict:
    """The Nucleus release record for one product/version/channel.

    `product` matters more than it looks: Nucleus carries a separate `Firefox for Android` release
    for the same version number, so defaulting to Firefox without saying so would answer an Android
    question with the desktop note set -- a wrong answer rather than an error.
    """
    releases = cached_json(NUCLEUS_RELEASES, "nucleus-releases.json", refresh)
    hits = [r for r in releases
            if r.get("product") == product and str(r.get("version")) == version
            and (channel is None or r.get("channel") == channel)]
    if hits and published_only:
        unpublished = [r for r in hits if not is_published(r)]
        hits = [r for r in hits if is_published(r)]
        if not hits and unpublished:
            sys.exit(f"error: {product} {version} exists in Nucleus but is not public yet, and "
                     "--published-only\nwas given. Drop that flag to review it as a draft.")
    if not hits:
        others = sorted({str(r.get("product")) for r in releases
                         if str(r.get("version")) == version})
        if others:
            sys.exit(f"error: no {product} release {version!r} in Nucleus; that version does exist "
                     f"for: {', '.join(others)} — pass --product")
        near = sorted({str(r.get("version")) for r in releases
                       if r.get("product") == product
                       and str(r.get("version", "")).startswith(version.split(".")[0])})
        sys.exit(f"error: no {product} release {version!r} in Nucleus"
                 + (f"; versions starting {version.split('.')[0]}: {', '.join(near)}" if near else ""))
    if len(hits) > 1:
        chans = ", ".join(sorted(str(r.get("channel")) for r in hits))
        sys.exit(f"error: {version} matches several channels ({chans}); pass --channel")
    return hits[0]


def scope_releases(release: dict, scope: str, refresh: bool,
                   published_only: bool = False) -> tuple[list[dict], list[dict]]:
    """The releases whose notes count as covering this flag value.

    The flag is **per major version** (`153+`), but a note may be attached to any release within
    that major -- the .0 or any later dot release. So checking one release against `153+` invents
    missing notes: bugs 2019042 and 2046183 were flagged `153+` and noted in 153.0.3, and a
    153.0-only comparison reported both as unnoted. Against a dot release it is far worse, since
    all 30 of the major's flagged bugs get compared to that dot release's handful of notes.

    Default therefore unions every release of the same major on the same channel. `--scope release`
    restricts it to the one release, which is what you want when asking "what did *this* release
    ship" rather than "was this bug ever noted".
    """
    if scope == "release":
        return [release], []
    major = str(release["version"]).split(".")[0]
    channel, product = release.get("channel"), release.get("product")
    all_rel = cached_json(NUCLEUS_RELEASES, "nucleus-releases.json", refresh)
    siblings = [r for r in all_rel
                if r.get("product") == product and r.get("channel") == channel
                and str(r.get("version", "")).split(".")[0] == major]
    # Unpublished releases are counted by default, because reviewing a release before it goes live
    # is the normal case and its notes are exactly what you are checking. Nucleus holds no abandoned
    # non-public records any more, so `is_public=False` now means "in flight" rather than "dead", and
    # excluding it would report every flagged bug as unnoted for the release being prepared.
    # `--published-only` answers the other question: what has actually shipped.
    drafts = [r for r in siblings if not is_published(r)]
    if published_only:
        return [r for r in siblings if is_published(r)], drafts
    return siblings, drafts


def notes_for(releases: list[dict], refresh: bool) -> list[dict]:
    notes = cached_json(NUCLEUS_NOTES, "nucleus-notes.json", refresh)
    targets = {f"https://nucleus.mozilla.org/rna/releases/{r['id']}/?format=json"
               for r in releases}
    return [n for n in notes
            if targets & set(n.get("releases") or []) and str(n.get("is_public")) == "True"]


def print_bugs(bugs: list[dict], version: int | None, limit: int) -> None:
    if not bugs:
        print("  (none)")
        return
    shown = bugs[:limit] if limit else bugs
    for b in shown:
        state = f"{b.get('status')} {b.get('resolution') or ''}".strip()
        extra = ""
        if version:
            extra = f"  fx{version}={b.get(f'cf_status_firefox{version}') or '---'}"
        print(f"  {b['id']}  [{b.get('cf_tracking_firefox_relnote')}]  "
              f"{b.get('product')}/{b.get('component')}  {state}{extra}")
        print(f"      {(b.get('summary') or '')[:100]}")
    if limit and len(bugs) > limit:
        print(f"  ... {len(bugs) - limit} older not shown (raise --limit, 0 for all, "
              "or use --format json)")


def cmd_coverage(args) -> None:
    release = nucleus_release(args.coverage, args.channel, args.refresh, args.product,
                             args.published_only)
    major = str(release["version"]).split(".")[0]
    nightly = str(release.get("channel")) == "Nightly"
    values = ([v.strip() for v in args.flags.split(",") if v.strip()] if args.flags
              else ([f"{major}+", "nightly+"] if nightly else [f"{major}+"]))

    scope = args.scope or ("release" if nightly else "major")
    flag_scoped = release.get("product") == "Firefox"
    releases, drafts = scope_releases(release, scope, args.refresh, args.published_only)
    notes = notes_for(releases, args.refresh)
    bugs = query(values, int(major) if major.isdigit() else None)
    by_id = {b["id"]: b for b in bugs}

    # Nucleus stores ONE `bug` per note, but a note can cite several in its text -- the 153.0.3
    # Smart Window note has bug=2019042 while its body links 2019042, 2044385 and 2046183. Using
    # only the field reported 2046183 as unnoted when it was covered by that very note.
    #
    # The two directions need different sets, which is why this is not one union:
    #   "flagged but no note"   -> primary + cited, or a note citing the bug is missed
    #   "noted but not flagged" -> primary only, since a supporting bug cited alongside a primary
    #                              is not itself evidence that the flag should have been set
    noted, cited, text_only, unnumbered = {}, {}, {}, []
    for n in notes:
        body = n.get("note") or ""
        links = [int(i) for i in re.findall(r"show_bug\.cgi\?id=(\d+)", body)]
        for i in links:
            cited.setdefault(i, []).append(n)
        if n.get("bug"):
            noted.setdefault(int(n["bug"]), []).append(n)
        elif links:
            # No `bug` field, but the body links one. Tracked separately so the three counts sum
            # to the note total and the reverse direction can still see it. The *first* link stands
            # in for the note's subject, which is how the notes doing this are written -- they lead
            # with the bug they are about and cite supporting bugs after it.
            text_only.setdefault(links[0], []).append(n)
        else:
            unnumbered.append(n)

    primary = {k: list(v) for k, v in noted.items()}
    for bug_id, ns in text_only.items():
        primary.setdefault(bug_id, []).extend(ns)
    covered = set(noted) | set(cited)
    flagged = set(by_id)
    missing_note = sorted(flagged - covered)
    unflagged_note = sorted(set(primary) - flagged)
    cited_only = sorted((covered - set(primary)) & flagged)

    related = ({} if (args.no_related or not flag_scoped)
               else related_coverage(missing_note, covered))
    unexplained = [i for i in missing_note if i not in related] if flag_scoped else []

    if args.format == "json":
        print(json.dumps({
            "release": {"product": release.get("product"), "version": release["version"],
                        "channel": release.get("channel"), "nucleus_id": release["id"]},
            "flag_values": values, "note_scope": scope,
            "notes": len(notes), "flagged": len(flagged),
            "flag_product_scoped": flag_scoped,
            # None distinguishes "not applicable for this product" from "none found".
            "flagged_but_no_note": unexplained if flag_scoped else None,
            "covered_via_related_bug": {str(k): v[0] for k, v in related.items()},
            "noted_but_not_flagged": unflagged_note,
            "matched_via_note_text_only": cited_only,
            "notes_without_bug": len(unnumbered),
        }, indent=2))
        return

    print(f"COVERAGE  {release.get('product')} {release['version']} {release.get('channel')} "
          f"(Nucleus release {release['id']})")
    print(f"          flag values compared: {', '.join(values)}")
    if scope == "major":
        vers = ", ".join(sorted(str(r["version"]) for r in releases))
        print(f"          note scope: every {major}.x {release.get('channel')} release, because the "
              f"flag is per-major\n                      and a note may sit on any of them "
              f"({len(releases)}: {vers})")
    else:
        print(f"          note scope: {release['version']} only")
    if drafts:
        names = ", ".join(sorted(str(r["version"]) for r in drafts))
        verb = "excluded" if args.published_only else "included"
        print(f"          {verb} {len(drafts)} unpublished (draft) release(s): {names}")
    print(f"\n  public notes           : {len(notes)}")
    print(f"    with a bug field     : {sum(len(v) for v in noted.values())}")
    print(f"    bug only in the text : {sum(len(v) for v in text_only.values())}")
    print(f"    no bug reference     : {len(unnumbered)}")
    print(f"  bugs carrying the flag : {len(flagged)}")
    print(f"  present in both        : {len(flagged & covered)}")
    if cited_only:
        print(f"    of which matched only by a bug link in a note's text, not its bug field: "
              f"{len(cited_only)}")

    # `cf_tracking_firefox_relnote` has no product dimension: a bug flagged `153+` is destined for
    # whichever product's notes Release Management chose, and in practice the flag tracks desktop.
    # Comparing it against another product's note set therefore says nothing -- checking Android
    # 153.0.3 this way reports all 30 desktop-flagged bugs as notes owed, "fn+f fullscreen on macOS"
    # among them. The reverse direction stays meaningful, so only this half is withheld.
    if not flag_scoped:
        print(f"\n  FLAGGED BUT NO NOTE -- not reported for {release.get('product')}: the relnote "
              f"flag is not\n  product-scoped, so the {len(flagged)} bug(s) flagged {', '.join(values)} are "
              f"not evidence about this\n  product's notes. Use --product Firefox for that "
              f"direction.")
    else:
        print(f"\n  FLAGGED BUT NO NOTE [{len(unexplained)}] -- a note may be owed:")
        # Newest first, matching what print_bugs says when it truncates -- and a recent gap is the
        # one worth acting on, so it must not be the one dropped.
        gaps = sorted((by_id[i] for i in unexplained),
                      key=lambda b: b.get("last_change_time") or "", reverse=True)
        print_bugs(gaps, int(major) if major.isdigit() else None, args.limit)

    if related:
        print(f"\n  COVERED VIA A RELATED BUG [{len(related)}] -- not gaps, but the flag and the "
              f"note sit on\n  different bugs (meta bug or rollup note):")
        shown = sorted(related.items())[:args.limit] if args.limit else sorted(related.items())
        for i, (via, how) in shown:
            print(f"  {i}  -> covered by a note citing bug {via} ({how})")
            print(f"      {(by_id[i].get('summary') or '')[:96]}")
        if args.limit and len(related) > args.limit:
            print(f"  ... {len(related) - args.limit} more")

    # Not all of this direction is a finding. Release Management authors dot-release `Fixed` notes
    # directly without setting the flag -- all eight bugs behind the 153.0.3 notes were `---` -- and
    # a known-issue note describes something *unfixed*, so it never carries a flag. Reporting those
    # as "flag may need setting" would bury the one or two that actually are.
    expected, real = [], []
    for i in unflagged_note:
        n = primary[i][0]
        tag = (n.get("tag") or "").lower()
        (expected if tag == "fixed" or str(n.get("is_known_issue")) == "True" else real).append(i)

    print(f"\n  NOTED BUT NOT FLAGGED [{len(real)}] -- the flag may genuinely need setting:")
    if real:
        for i in real:
            n = primary[i][0]
            print(f"  {i}  [{n.get('tag') or 'untagged'}]  {(n.get('note') or '')[:88]}")
    else:
        print("  (none)")
    if expected:
        print(f"\n  ({len(expected)} further unflagged note(s) are `Fixed` or known-issue notes, "
              f"which Release\n   Management authors without the flag -- expected, not a gap.)")

    if unnumbered:
        print(f"\n  NOTES WITH NO BUG NUMBER [{len(unnumbered)}] -- cannot be cross-checked:")
        for n in unnumbered:
            print(f"      [{n.get('tag') or 'untagged'}] {(n.get('note') or '')[:88]}")

    print("\n  Caveat: security-restricted bugs are omitted by REST with no count, so this "
          "cannot\n  prove a flagged bug is absent -- only that none is visible to you.")

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    m = p.add_mutually_exclusive_group(required=True)
    m.add_argument("--nominated", action="store_true", help="flag is '?' (awaiting a decision)")
    m.add_argument("--approved", metavar="N", help="flag is 'N+' (approved for release N)")
    m.add_argument("--nightly", action="store_true", help="flag is 'nightly+'")
    m.add_argument("--declined", action="store_true", help="flag is '-'")
    m.add_argument("--value", help="raw flag value, escaping handled for you")
    m.add_argument("--coverage", metavar="VERSION",
                   help="compare a shipped note set against the flagged bugs (e.g. 155.0a1)")
    p.add_argument("--published-only", action="store_true",
                   help="with --coverage, count only notes on releases that are already public; "
                        "the default includes drafts, since reviewing before a release goes live "
                        "is the normal case")
    p.add_argument("--include-open", action="store_true",
                   help="with --nominated, also list bugs that are not fixed yet")
    p.add_argument("--channel", help="disambiguate --coverage when a version has several")
    p.add_argument("--product", default="Firefox",
                   help="Nucleus product for --coverage: 'Firefox', 'Firefox for Android', "
                        "'Firefox for iOS' (default: Firefox)")
    p.add_argument("--flags", help="override the flag values --coverage compares")
    p.add_argument("--scope", choices=["release", "major"], default=None,
                   help="notes counted as coverage: this release only, or every release of the "
                        "same major (default: major on Release/Beta, release on Nightly)")
    p.add_argument("--version", type=int, default=None,
                   help="also show cf_status_firefoxN (default: current Nightly)")
    p.add_argument("--limit", type=int, default=60, help="rows to print; 0 for all")
    p.add_argument("--refresh", action="store_true", help="bypass the Nucleus cache")
    p.add_argument("--no-related", action="store_true",
                   help="skip the blocks/depends_on hop that explains meta-bug and rollup coverage")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args()

    if args.approved and not str(args.approved).isdigit():
        p.error(f"--approved takes a major version number, not {args.approved!r}")

    if args.coverage:
        cmd_coverage(args)
        return

    # These only mean something to --coverage. Accepting and ignoring them would quietly answer a
    # different question than the one asked -- `--nominated --product "Firefox for Android"` reads
    # like a scoped nomination list and would silently return the unscoped one.
    coverage_only = {"product": "Firefox", "channel": None, "scope": None, "flags": None,
                     "no_related": False, "refresh": False, "published_only": False}
    misused = [f"--{k.replace('_', '-')}" for k, default in coverage_only.items()
               if getattr(args, k) != default]
    if misused:
        p.error(f"{', '.join(misused)} only applies to --coverage")
    if args.include_open and not args.nominated:
        p.error("--include-open only applies to --nominated")

    if args.nominated:
        values, label = ["?"], "nominated (flag '?')"
    elif args.approved:
        values, label = [f"{args.approved}+"], f"approved for {args.approved}"
    elif args.nightly:
        values, label = ["nightly+"], "Nightly-only notes"
    elif args.declined:
        values, label = ["-"], "declined"
    else:
        values, label = [args.value], f"flag {args.value!r}"

    version = args.version
    if version is None:
        try:
            version = trainlib.train_versions()["nightly"]
        except (RuntimeError, KeyError) as e:
            print(f"# WARNING: cannot resolve the current Nightly ({e}); omitting the "
                  "cf_status_firefoxN column", file=sys.stderr)

    bugs = query(values, version)
    total = len(bugs)
    dropped = 0
    if args.nominated and not args.include_open:
        keep = [b for b in bugs if is_fixed(b)]
        dropped = total - len(keep)
        bugs = keep
    # Newest first: truncation drops the tail, and for a 234-entry list like --declined the useful
    # end is the recent decisions, not bugs last touched in 2012.
    bugs.sort(key=lambda b: b.get("last_change_time") or "", reverse=True)

    if args.format == "json":
        print(json.dumps({"flag_values": values, "count": len(bugs), "bugs": bugs}, indent=2))
        return

    print(f"{label}: {len(bugs)} bug(s)")
    if dropped:
        print(f"  ({dropped} not-yet-fixed nomination(s) hidden; --include-open to show them)")
    print()
    print_bugs(bugs, version, args.limit)


if __name__ == "__main__":
    main()
