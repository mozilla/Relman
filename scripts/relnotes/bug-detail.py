#!/usr/bin/env python3
"""Print the fields needed to judge a release-note candidate, for one or more bugs.

Exists to replace ad-hoc `curl ... | python3 -c ...` one-liners. Those are fine once, but every
invocation has a slightly different shape, so each one triggers a fresh permission prompt and the
workflow never settles. A stable script under scripts/relnotes/ is allowlisted by prefix and stops
interrupting.

Shows, per bug: per-version status flags across the live trains, the relnote flag, reporter (and
whether they are internal), duplicate/see_also counts, keywords, whether the bug is public, and any
pending uplift approval requests -- i.e. the impact-evidence and gating signals the skill weighs.

Usage:
  bug-detail.py 2046143
  bug-detail.py 2046143 2056032 2057384
  bug-detail.py 2046143 --comments        # also comment 0 and the newest comment
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
        "cf_tracking_firefox_relnote", "assigned_to",
    ] + version_fields(trains)
    qs = url_parse.urlencode({"id": ",".join(bug_ids), "include_fields": ",".join(fields)})
    return trainlib.fetch_json(f"{REST}/bug?{qs}").get("bugs", [])


def pending_uplifts(bug_id: str) -> list[str]:
    try:
        payload = trainlib.fetch_json(f"{REST}/bug/{bug_id}/attachment?exclude_fields=data")
    except RuntimeError:
        return []
    out = []
    for atts in (payload.get("bugs") or {}).values():
        for a in atts:
            for fl in a.get("flags", []):
                if fl.get("name", "").startswith("approval-mozilla-") and fl.get("status") == "?":
                    t = fl["name"].replace("approval-mozilla-", "")
                    if t not in out:
                        out.append(t)
    return out


def comments(bug_id: str) -> list[dict]:
    try:
        payload = trainlib.fetch_json(f"{REST}/bug/{bug_id}/comment")
    except RuntimeError:
        return []
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
    if args.landings:
        if ".." not in args.landings:
            p.error("--landings takes a git range, e.g. --landings 28b5e86da948..fcddc9cb649c")
        nonnumeric = [i for i in ids if not i.isdigit()]
        if nonnumeric:
            p.error(f"--landings matches commit subjects by bug number; not a bug id: "
                    f"{', '.join(nonnumeric)}")
        # These belong to the judgment-fields output. Accepting and ignoring them would answer a
        # different question than the one asked.
        ignored = [f"--{n}" for n in ("full", "comments") if getattr(args, n)]
        if ignored:
            p.error(f"{', '.join(ignored)} does not apply to --landings")
        landings(trainlib.resolve_repo(args.repo), ids, args.landings)
        return
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
        creator = b.get("creator") or ""
        internal = creator.endswith("@mozilla.com")
        print(f"    reporter: {creator} ({'internal' if internal else 'EXTERNAL'})"
              f"   filed {(b.get('creation_time') or '')[:10]}")
        # see_also holds URLs rather than bug ids, so it is reported as a count only.
        for rel_field, label in (("duplicates", "duplicates"), ("blocks", "blocks")):
            ids = b.get(rel_field) or []
            if not ids:
                continue
            try:
                qs2 = url_parse.urlencode({"id": ",".join(str(i) for i in ids[:8]),
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
        if up:
            print(f"    PENDING UPLIFT REQUESTS: {', '.join(up)}")
        if args.comments:
            cs = comments(bid)
            if cs:
                first = " ".join(cs[0]["text"].split())
                print(f"    comment 0 ({cs[0]['creator']}): {first[:400]}")
                if len(cs) > 1:
                    last = " ".join(cs[-1]["text"].split())
                    print(f"    newest #{cs[-1]['count']} ({cs[-1]['creator']}): {last[:400]}")
        print()

    if missing:
        print(f"NOT RETURNED (security-restricted or nonexistent): {', '.join(missing)}")


if __name__ == "__main__":
    main()
