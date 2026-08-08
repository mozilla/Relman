#!/usr/bin/env python3
"""Print the fields needed to judge a release-note candidate, for one or more bugs.

Exists to replace ad-hoc `curl ... | python3 -c ...` one-liners. Those are fine once, but every
invocation has a slightly different shape, so each one triggers a fresh permission prompt and the
workflow never settles. A stable script under scripts/relnotes/ is allowlisted by prefix and stops
interrupting.

Shows, per bug: per-version status flags across the live trains, the relnote flag, open needinfo
requests, reporter (and whether they are internal), duplicate/see_also counts, keywords, whether the
bug is public, and any pending uplift approval requests -- i.e. the impact-evidence and gating signals
the skill weighs.

The `open needinfo:` line prints even when there is nothing open, and that is deliberate: telling the
user to "close the needinfo" or "withdraw the ask" is a claim about live Bugzilla state, and an absent
line cannot be distinguished from a check nobody ran. A pass once sourced that claim from a watchlist
status instead -- the needinfo had already been cleared. A watchlist status records what we did; only
Bugzilla knows what is still open.

Usage:
  bug-detail.py 2046143
  bug-detail.py 2046143 2056032 2057384
  bug-detail.py 2046143 --comments        # also comment 0 and the newest comment
  bug-detail.py 2046143 --comment 16      # one comment in full, untruncated (also 15,16,17)
  bug-detail.py 2046143 --full            # every field, as JSON
"""

import argparse
import json
import re
import sys
import urllib.parse as url_parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

REST = "https://bugzilla.mozilla.org/rest"


def version_fields(trains: dict) -> list[str]:
    out = []
    for v in (trains.get("nightly"), trains.get("beta"), trains.get("release")):
        if v:
            out.append(f"cf_status_firefox{v}")
    for v in trains.get("esr") or []:
        out.append(f"cf_status_firefox_esr{v}")
    return out


def fetch(bug_ids: list[str], trains: dict) -> list[dict]:
    fields = [
        "id", "summary", "product", "component", "status", "resolution", "keywords",
        "creator", "creation_time", "op_sys", "version", "whiteboard", "groups",
        "duplicates", "see_also", "blocks", "depends_on", "regressed_by",
        "cf_tracking_firefox_relnote", "assigned_to", "flags",
    ] + version_fields(trains)
    qs = url_parse.urlencode({"id": ",".join(bug_ids), "include_fields": ",".join(fields)})
    return trainlib.fetch_json(f"{REST}/bug?{qs}").get("bugs", [])


def pending_uplifts(bug_id: str) -> list[str] | None:
    """Targets with a pending `approval-mozilla-*` request, or None if the fetch failed.

    None rather than an empty list, because pending-uplift is used to *reject* candidates and to
    decide which version a note belongs to: reporting a failed fetch as "nothing pending" changes
    the verdict silently. `daily-pass.py` takes the same position for the same reason.
    """
    try:
        payload = trainlib.fetch_json(f"{REST}/bug/{bug_id}/attachment?exclude_fields=data")
    except RuntimeError as e:
        print(f"# WARNING: could not read attachments for bug {bug_id} ({e}); its pending-uplift "
              "state is unknown", file=sys.stderr)
        return None
    out = []
    for atts in (payload.get("bugs") or {}).values():
        for a in atts:
            for fl in a.get("flags", []):
                if fl.get("name", "").startswith("approval-mozilla-") and fl.get("status") == "?":
                    t = fl["name"].replace("approval-mozilla-", "")
                    if t not in out:
                        out.append(t)
    return out


def open_needinfos(bug: dict) -> list[str]:
    """Open needinfo requests on the bug, formatted for display.

    Bugzilla models needinfo as a bug flag with status "?", where the requestee is whoever owes
    the answer. Everything else in `flags` is a different kind of state (tracking, approvals on
    attachments) and is not a question waiting on someone.
    """
    out = []
    for f in bug.get("flags") or []:
        if f.get("name") == "needinfo" and f.get("status") == "?":
            who = f.get("requestee") or "unspecified"
            set_on = (f.get("creation_date") or "")[:10]
            out.append(f"{who}" + (f" (set {set_on})" if set_on else ""))
    return out


def comments(bug_id: str) -> list[dict] | None:
    """Every comment on the bug, or None if the fetch failed.

    None rather than an empty list: a swallowed failure made `--comment 16` print "NOT FOUND",
    which asserts something about the bug's contents on the strength of a 503.
    """
    try:
        payload = trainlib.fetch_json(f"{REST}/bug/{bug_id}/comment")
    except RuntimeError as e:
        print(f"# WARNING: could not read comments for bug {bug_id} ({e})", file=sys.stderr)
        return None
    for v in (payload.get("bugs") or {}).values():
        return v.get("comments", [])
    return []


def landings(repo, bug_ids: list[str], rev_range: str) -> None:
    """Each landing for these bugs in a git range, with its diffstat.

    Composing this by hand needs command substitution --
    `git show --stat $(git log --format=%H <range> --grep=<bug>)` -- and a `$(...)` subshell cannot
    be matched against a permission allowlist prefix, so every such call prompts. Doing the two
    steps in-process removes both the prompt and the composition.
    """
    # Validate the range once, so a typo is reported as a bad range rather than surfacing as
    # "no landing" for every bug. Below, check=True is deliberate: an empty match exits 0, so only a
    # real git failure raises -- reporting one as an empty result is how a bad range looks like a
    # quiet window.
    try:
        trainlib.git(repo, "rev-list", "--count", rev_range, check=True)
    except RuntimeError as e:
        sys.exit(f"error: {e}")

    for bug in bug_ids:
        try:
            out = trainlib.git(repo, "log", "--format=%H\t%h\t%ad\t%s", "--date=short",
                               rev_range, f"--grep={bug}", check=True).strip()
        except RuntimeError as e:
            sys.exit(f"error: {e}")
        if not out:
            print(f"=== bug {bug}: no landing in {rev_range}")
            continue
        # --grep matches the whole message, so a commit that merely *references* this bug in its
        # body comes back too. A landing is one whose subject names the bug; the rest are
        # cross-references and are counted rather than listed, so neither is silently mixed in.
        all_rows = [ln.split("\t", 3) for ln in out.splitlines()]
        # re.escape, because the id is external input: an unescaped `205814.` matched bug
        # 2058143 and reported ten of its landings under the wrong number.
        pat = re.compile(rf"\bbug\s+{re.escape(bug)}\b", re.IGNORECASE)
        rows = [r for r in all_rows if pat.search(r[3])]
        refs = len(all_rows) - len(rows)
        if not rows:
            print(f"=== bug {bug}: no landing in {rev_range}"
                  + (f" ({refs} commit(s) reference it without naming it in the subject)" if refs else ""))
            continue
        print(f"=== bug {bug}: {len(rows)} landing(s) in {rev_range}"
              + (f"  (+{refs} commit(s) only referencing it)" if refs else ""))
        for full, short, date, subject in rows:
            print(f"  {short}  {date}  {subject}")
            stat = trainlib.git(repo, "show", "--stat=140", "--format=", full,
                                check=True).strip()
            for line in stat.splitlines():
                print(f"      {line}")
        print()


def main() -> None:
    p = argparse.ArgumentParser(description="Judgment fields for release-note candidates.")
    p.add_argument("bugs", nargs="+")
    p.add_argument("--comments", action="store_true", help="also show comment 0 and the newest")
    p.add_argument("--comment", metavar="N",
                   help="show these comments in full, untruncated, keeping their line breaks "
                        "(one number, or 15,16,17). --comments truncates to 400 characters, which "
                        "cuts a developer's reasoning off mid-sentence. Numbers are per bug, so "
                        "these are only comparable across a batch for --comment 0, which is every "
                        "bug's description")
    p.add_argument("--full", action="store_true", help="dump raw JSON")
    p.add_argument("--width", type=int, default=110)
    p.add_argument("--landings", metavar="A..B",
                   help="instead of the judgment fields, show each bug's landings in this git "
                        "range with their diffstat")
    p.add_argument("--repo", default=None, help="Gecko checkout (default: saved by check-setup)")
    args = p.parse_args()

    # Accept "123 456" and "123,456" alike; the comma form previously arrived as a single
    # argument, so every id was reported as NOT RETURNED even though all were fetched.
    ids = [i for arg in args.bugs for i in str(arg).replace(",", " ").split() if i]
    args.bugs = ids
    wanted_comments: list[int] = []
    if args.comment:
        nums = [n for n in str(args.comment).replace(",", " ").split() if n]
        bad = [n for n in nums if not n.isdigit()]
        if bad:
            p.error(f"--comment takes comment numbers, e.g. --comment 16 or --comment 15,16,17; "
                    f"not: {', '.join(bad)}")
        wanted_comments = [int(n) for n in nums]
    if args.landings:
        if ".." not in args.landings:
            p.error("--landings takes a git range, e.g. --landings 28b5e86da948..fcddc9cb649c")
        nonnumeric = [i for i in ids if not i.isdigit()]
        if nonnumeric:
            p.error(f"--landings matches commit subjects by bug number; not a bug id: "
                    f"{', '.join(nonnumeric)}")
        # These belong to the judgment-fields output. Accepting and ignoring them would answer a
        # different question than the one asked.
        ignored = [f"--{n}" for n in ("full", "comments", "comment") if getattr(args, n)]
        if ignored:
            p.error(f"{', '.join(ignored)} does not apply to --landings")
        landings(trainlib.resolve_repo(args.repo), ids, args.landings)
        return
    # Comments live on their own Bugzilla endpoint and are not part of the bug JSON, so --full
    # cannot answer them. Rejecting the combination rather than dropping it keeps this consistent
    # with --landings above: a flag that would be silently ignored is a question left unanswered.
    if args.full:
        ignored = [f"--{n}" for n in ("comments", "comment") if getattr(args, n)]
        if ignored:
            p.error(f"{', '.join(ignored)} does not apply to --full; comments come from a "
                    f"separate endpoint and are not part of the bug JSON")
    trains = trainlib.train_versions()
    bugs = fetch(ids, trains)
    if args.full:
        print(json.dumps(bugs, indent=2))
        return
    found = {str(b["id"]) for b in bugs}
    missing = [b for b in args.bugs if str(b) not in found]

    for b in sorted(bugs, key=lambda x: str(x["id"])):
        bid = str(b["id"])
        print(f"=== {bid}  {b['product']} :: {b['component']}")
        print(f"    {b['summary'][:args.width]}")
        flags = []
        for f in version_fields(trains):
            val = b.get(f)
            if val and val != "---":
                flags.append(f"{f.replace('cf_status_firefox', 'fx')}={val}")
        print(f"    {b['status']}/{b.get('resolution') or '-'}   " + "  ".join(flags))
        rel = b.get("cf_tracking_firefox_relnote")
        if rel and rel != "---":
            print(f"    relnote flag: {rel}")
        # Printed even when empty -- see the module docstring. "none" is the answer to
        # "is anything still outstanding here?", and it has to be visibly answered.
        ni = open_needinfos(b)
        print(f"    open needinfo: {'; '.join(ni) if ni else 'none'}")
        creator = b.get("creator") or ""
        internal = creator.endswith("@mozilla.com")
        print(f"    reporter: {creator} ({'internal' if internal else 'EXTERNAL'})"
              f"   filed {(b.get('creation_time') or '')[:10]}")
        # see_also holds URLs rather than bug ids, so it is reported as a count only.
        for rel_field, label in (("duplicates", "duplicates"), ("blocks", "blocks")):
            # Not `ids`: that name holds the bug ids this run was asked about.
            rel_ids = b.get(rel_field) or []
            if not rel_ids:
                continue
            try:
                qs2 = url_parse.urlencode({"id": ",".join(str(i) for i in rel_ids[:8]),
                                           "include_fields": "id,summary,status,resolution"})
                rel_bugs = trainlib.fetch_json(f"{REST}/bug?{qs2}").get("bugs", [])
            except RuntimeError:
                continue
            for rb in rel_bugs:
                print(f"    {label}: {rb['id']} [{rb['status']}] {rb['summary'][:78]}")
        ev = (f"dupes={len(b.get('duplicates') or [])} "
              f"see_also={len(b.get('see_also') or [])} "
              f"blocks={len(b.get('blocks') or [])} "
              f"public={not b.get('groups')}")
        print(f"    impact evidence: {ev}")
        if b.get("keywords"):
            print(f"    keywords: {', '.join(b['keywords'])}")
        if b.get("regressed_by"):
            # Resolve the regressor and show how old it is. A regression whose cause
            # landed years ago has, by evidence, gone largely unnoticed -- that is a
            # strong argument against a note however bad the symptom sounds.
            try:
                qs3 = url_parse.urlencode({
                    "id": ",".join(str(i) for i in b["regressed_by"][:5]),
                    "include_fields": "id,summary,creation_time,cf_last_resolved"})
                for rb in trainlib.fetch_json(f"{REST}/bug?{qs3}").get("bugs", []):
                    landed = (rb.get("cf_last_resolved") or rb.get("creation_time") or "")[:10]
                    age = ""
                    if landed and b.get("creation_time"):
                        try:
                            import datetime as _dt
                            d1 = _dt.date.fromisoformat(landed)
                            d2 = _dt.date.fromisoformat(b["creation_time"][:10])
                            years = (d2 - d1).days / 365.25
                            age = f"  -- broken for ~{years:.1f} years before being reported"
                        except ValueError:
                            pass
                    print(f"    regressed_by: {rb['id']} (landed {landed}){age}")
                    print(f"        {rb['summary'][:80]}")
            except RuntimeError:
                print(f"    regressed_by: {b['regressed_by']}")
        if b.get("whiteboard"):
            print(f"    whiteboard: {b['whiteboard'][:args.width]}")
        if b.get("op_sys") and b["op_sys"] not in ("Unspecified", "All"):
            print(f"    os: {b['op_sys']}")
        up = pending_uplifts(bid)
        if up is None:
            print("    PENDING UPLIFT REQUESTS: UNKNOWN -- the attachment fetch failed")
        elif up:
            print(f"    PENDING UPLIFT REQUESTS: {', '.join(up)}")
        # One fetch serves both flags; they read the same endpoint.
        cs = comments(bid) if (args.comments or wanted_comments) else []
        # Per bug, not per run: one bug's failed fetch must not disable the flag for the rest.
        unread = cs is None
        if unread:
            # Say it here and not only on stderr: this line travels with the bug in a pasted
            # report, where "no Preconditions block" and "never read" look identical.
            print("    comments: COULD NOT READ -- nothing below is evidence about this bug's "
                  "comments")
            cs = []
        if args.comments and cs:
            first = " ".join(cs[0]["text"].split())
            print(f"    comment 0 ({cs[0]['creator']}): {first[:400]}")
            if len(cs) > 1:
                last = " ".join(cs[-1]["text"].split())
                print(f"    newest #{cs[-1]['count']} ({cs[-1]['creator']}): {last[:400]}")
        if wanted_comments and not unread:
            by_count = {c["count"]: c for c in cs}
            for n in wanted_comments:
                c = by_count.get(n)
                if not c:
                    highest = max(by_count) if by_count else "none"
                    print(f"    comment #{n}: NOT FOUND (highest comment number is {highest})")
                    continue
                print(f"    comment #{n} ({c['creator']}, {(c.get('creation_time') or '')[:10]}):")
                for line in c["text"].splitlines():
                    print(f"        {line}")
        print()

    if missing:
        print(f"NOT RETURNED (security-restricted or nonexistent): {', '.join(missing)}")


if __name__ == "__main__":
    main()
