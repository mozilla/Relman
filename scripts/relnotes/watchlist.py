#!/usr/bin/env python3
"""Track release-note work across a cycle: local, per-user, organised by release.

A daily scan only sees one build, but release-note work is a *cycle-long* activity: a feature
accumulates behind a preference for weeks, a developer is pinged on Tuesday and replies on Friday,
a Nightly-only note has to be revisited when the feature rides the train. Keyed by release, this
gives one place to see everything done for 155 from merge day to ship day.

State lives beside the scan watermark in the per-user state directory, deliberately **not** in the
repo: team workflows differ per person, and a shared tracked file would create merge noise and
imply consensus that isn't there.

Schema (v2):
    {"version": 2, "releases": {"155": {"items": {...}, "days_reviewed": [...]}}}

v1 state (a flat "items" map) is migrated automatically, filing each item under its `target` or
under the current Nightly.

Usage:
  watchlist.py list                       # current Nightly's release
  watchlist.py list --release 156
  watchlist.py list --all-releases
  watchlist.py add 2051691 --status gated --note "referrals, pref off all channels"
  watchlist.py add nova --release 156 --kind feature --status nightly-note-requested --note "..."
  watchlist.py note 2051354 "reporter says impact is broader: also Google Sheets"
  watchlist.py replied 2051354           # developer answered
  watchlist.py done 2051691
  watchlist.py days 20260731             # record a reviewed Nightly day
  watchlist.py summary                   # per-release counts
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

WATCHLIST_FILE = trainlib.STATE_DIR / "watchlist.json"

STATUSES = [
    "watching",                 # keep an eye on it, no action yet
    "gated",                    # landed but not reaching users
    "asked",                    # developer has been pinged
    "replied",                  # developer answered; needs your follow-up
    "nightly-note-requested",   # a nightly+ note has been asked for
    "note-requested",           # a release note has been asked for
    "noted",                    # note is in Nucleus
    "done",                     # resolved; kept for history
    "declined",                 # decided against a note
]
CLOSED = ("done", "declined", "noted")


def now() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def current_release(required: bool = False) -> str:
    """The current Nightly major, from product-details.

    Pass `required=True` from anything that will *persist* a release key. Filing a cycle's work
    under a literal "unknown" because product-details blipped for thirty seconds is worse than
    refusing: `resume` for the real release never shows it again, and nothing on screen said so.
    Read-only callers can tolerate the placeholder.
    """
    try:
        return str(trainlib.train_versions()["nightly"])
    except (RuntimeError, KeyError) as e:
        if required:
            sys.exit(f"error: cannot determine the current Nightly ({e}).\n"
                     "Pass --release <N> so this is not filed under an unknown release.")
        return "unknown"


def load() -> dict:
    if not WATCHLIST_FILE.exists():
        return {"version": 2, "releases": {}}
    try:
        data = json.loads(WATCHLIST_FILE.read_text())
    except ValueError:
        sys.exit(f"error: {WATCHLIST_FILE} is not valid JSON")
    if data.get("version") == 2:
        data.setdefault("releases", {})
        return data
    # Migrate v1: a flat {"items": {...}} map with no release dimension.
    rel_default = current_release()
    out: dict = {"version": 2, "releases": {}}
    for key, item in (data.get("items") or {}).items():
        rel = str(item.get("target") or rel_default)
        bucket = out["releases"].setdefault(rel, {"items": {}, "days_reviewed": []})
        bucket["items"][key] = item
    # The old fake "days-reviewed" item becomes real per-release state.
    for rel, bucket in out["releases"].items():
        marker = bucket["items"].pop("days-reviewed", None)
        if marker:
            import re
            bucket["days_reviewed"] = sorted(set(re.findall(r"\b20\d{6}\b",
                                                            marker.get("summary", ""))))
    print(f"# migrated watchlist to v2 ({sum(len(b['items']) for b in out['releases'].values())} "
          f"items across {len(out['releases'])} release(s))", file=sys.stderr)
    return out


def save(data: dict) -> None:
    trainlib.write_json_atomic(WATCHLIST_FILE, data)


def bucket(data: dict, release: str) -> dict:
    b = data["releases"].setdefault(release, {"items": {}, "days_reviewed": []})
    b.setdefault("log", [])
    return b


def find(data: dict, key: str, release: str | None) -> tuple[str, dict] | None:
    """Locate an item, preferring the given release but searching all of them."""
    if release and key in data["releases"].get(release, {}).get("items", {}):
        return release, data["releases"][release]["items"][key]
    for rel, b in data["releases"].items():
        if key in b["items"]:
            return rel, b["items"][key]
    return None


def cmd_add(args) -> None:
    data = load()
    rel = args.release or current_release(required=True)
    key = str(args.key)
    b = bucket(data, rel)
    item = b["items"].get(key, {"added": now(), "log": []})
    item.update({
        "kind": args.kind or item.get("kind", "bug" if key.isdigit() else "feature"),
        "status": args.status or item.get("status", "watching"),
        "summary": args.note or item.get("summary", ""),
        "updated": now(),
    })
    if args.due:
        item["due"] = args.due
    if args.note:
        item["log"].append({"date": now(), "text": args.note})
    b["items"][key] = item
    save(data)
    print(f"[{rel}] {key}: {item['status']}")


def cmd_note(args) -> None:
    data = load()
    hit = find(data, str(args.key), args.release)
    if not hit:
        sys.exit(f"error: {args.key} is not tracked (use `add` first)")
    rel, item = hit
    item.setdefault("log", []).append({"date": now(), "text": args.text})
    item["updated"] = now()
    save(data)
    print(f"[{rel}] noted on {args.key}")


def set_status(args, status: str) -> None:
    data = load()
    hit = find(data, str(args.key), args.release)
    if not hit:
        sys.exit(f"error: {args.key} is not tracked")
    rel, item = hit
    item["status"] = status
    item["updated"] = now()
    item.setdefault("log", []).append({"date": now(), "text": f"status -> {status}"})
    save(data)
    print(f"[{rel}] {args.key} -> {status}")


def cmd_rm(args) -> None:
    data = load()
    hit = find(data, str(args.key), args.release)
    if not hit:
        sys.exit(f"error: {args.key} is not tracked")
    rel, _ = hit
    data["releases"][rel]["items"].pop(str(args.key))
    save(data)
    print(f"[{rel}] removed {args.key}")


def cmd_log(args) -> None:
    """Append release-level context that belongs to no single bug.

    The items map is keyed by bug or feature, so there is nowhere to put things like
    "reviewed the Nightly notes today, two wording items still open". That context is
    exactly what a session loses to compaction, so it needs a home on disk.
    """
    data = load()
    rel = args.release or current_release(required=True)
    b = bucket(data, rel)
    if args.text:
        b["log"].append({"date": now(), "text": args.text})
        save(data)
        print(f"[{rel}] logged")
    for e in b["log"][-12:]:
        print(f"  {e['date']}  {e['text']}")


def cmd_resume(args) -> None:  # noqa: C901
    """Everything needed to pick up mid-cycle in a fresh or post-compaction session.

    A release manager may work one long session across a whole cycle, but that session
    will be compacted, and may be replaced. This is the briefing that survives either:
    where the scan got to, what has been reviewed, what is owed a follow-up, and what is
    being deliberately held.
    """
    data = load()
    rel = args.release or current_release()
    b = data["releases"].get(rel, {"items": {}, "days_reviewed": [], "log": []})
    items = b.get("items", {})

    print(f"=== RESUME BRIEFING — Firefox {rel} ===\n")

    repo = trainlib.resolve_repo(args.repo)
    st = trainlib.watermark_status(repo, trainlib.read_watermark(),
                                   int(rel) if rel.isdigit() else 0)
    if st.get("known"):
        stale = "  *** STALE: predates the current train ***" if st.get("stale_train") else ""
        print(f"SCAN POSITION  watermark {st['commit'][:12]} ({st['date']}), "
              f"{st['commits_behind']} commits behind origin/main{stale}")
        print(f"               resume with: daily-pass.py --since-last --save-state --brief")
    else:
        print("SCAN POSITION  no usable watermark; run scan-window.py --show-state and pick a build")
    days = b.get("days_reviewed", [])
    print(f"DAYS REVIEWED  {len(days)}"
          + (f"  ({days[0]} .. {days[-1]})" if days else ""))
    print()

    by_status: dict[str, list[str]] = {}
    for k, v in items.items():
        by_status.setdefault(v.get("status", "?"), []).append(k)
    print("STATUS         " + ("; ".join(f"{len(v)} {k}" for k, v in sorted(by_status.items()))
                               or "nothing tracked"))
    print()

    def show(status, header):
        rows = sorted(k for k, v in items.items() if v.get("status") == status)
        if not rows:
            return
        print(header)
        for k in rows:
            it = items[k]
            due = f"  [follow up after {it['due']}]" if it.get("due") else ""
            print(f"  {k}{due}  {it.get('summary','')[:100]}")
        print()

    show("replied", "NEEDS YOUR FOLLOW-UP (developer answered):")
    show("asked", "AWAITING A REPLY:")
    show("nightly-note-requested", "NIGHTLY NOTE REQUESTED:")
    show("watching", "HELD DELIBERATELY (revisit at cycle end):")
    show("gated", "GATED:")

    if b.get("log"):
        print("RECENT CONTEXT:")
        for e in b["log"][-8:]:
            print(f"  {e['date']}  {e['text']}")
        print()
    print("Run `watchlist.py followup` for live relnote-flag state on the asked/replied items.")


# Read-only git subcommands the release-note passes actually run against the Gecko clone.
# Deliberately enumerated rather than allowing "git -C <repo>:*", which would also pre-approve
# checkout/reset/commit against someone's working tree.
GECKO_GIT_SUBCOMMANDS = ("log", "show", "diff", "grep", "rev-list", "rev-parse", "merge-base",
                         "for-each-ref", "tag", "fetch", "ls-tree", "cat-file")
REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_SETTINGS = REPO_ROOT / ".claude" / "settings.local.json"
SHARED_SETTINGS = REPO_ROOT / ".claude" / "settings.json"


def _allow_entries(repo: Path) -> list[str]:
    """Permission entries for reads against this clone.

    A path containing whitespace has to be quoted on the command line, and a quoted command no
    longer shares a literal prefix with an unquoted entry -- so both spellings are emitted. Without
    that, check-setup would report success while every gecko read kept prompting.
    """
    forms = [str(repo)] + ([f'"{repo}"'] if any(c.isspace() for c in str(repo)) else [])
    return [f"Bash(git -C {form} {sub}:*)" for form in forms for sub in GECKO_GIT_SUBCOMMANDS]


def cmd_check_setup(args) -> None:
    """Resolve the Gecko checkout once, save it, and report the permission entries it needs.

    The clone lives somewhere different on every machine, so neither the scripts nor the
    shared settings.json can hardcode it. This is the one place that learns the path.
    """
    if args.repo:
        candidate = Path(args.repo).expanduser()
        if not trainlib.is_gecko_checkout(candidate):
            why = ("no Gecko source in it" if (candidate / ".git").exists()
                   else "not a git checkout")
            sys.exit(f"error: {candidate} is {why}; expected to find {trainlib.GECKO_MARKER}")
        repo = candidate.resolve()
        path = trainlib.write_config(gecko_repo=str(repo))
        print(f"GECKO CHECKOUT  {repo}\n                saved to {path}")
    else:
        # exits with instructions if it cannot be found
        repo, origin = trainlib.resolve_repo_with_source(None)
        saved = trainlib.read_config().get("gecko_repo")
        if not saved:
            origin += " — not saved, so every other script falls back to the same guess"
        print(f"GECKO CHECKOUT  {repo}\n                source: {origin}")
        if not saved:
            print("                save it with: check-setup --repo "
                  f"{repo}")

    # A malformed settings file must not read as "no entries present": the entries may all be
    # there, and reporting them missing sends the reader after the wrong problem. Matches the
    # write path below, which refuses rather than overwriting.
    allowed = set()
    for f in (SHARED_SETTINGS, LOCAL_SETTINGS):
        if not f.exists():
            continue
        try:
            allowed |= set(json.loads(f.read_text()).get("permissions", {}).get("allow", []))
        except ValueError as e:
            sys.exit(f"error: {f} is not valid JSON ({e}); cannot tell which permission "
                     "entries are present. Fix it, then re-run.")
    wanted = _allow_entries(repo)
    missing = [e for e in wanted if e not in allowed]

    if not missing:
        print(f"\nPERMISSIONS     all {len(wanted)} gecko git entries present")
        return

    print(f"\nPERMISSIONS     {len(missing)} of {len(wanted)} entries missing — "
          "gecko reads will prompt on every call")
    for e in missing:
        print(f"                {e}")
    if not args.write:
        print(f"\nAdd them with:  check-setup --repo {repo} --write")
        print(f"                (writes {LOCAL_SETTINGS.name}, which is git-ignored, so the "
              "path stays off the shared tree)")
        return

    # Absent and malformed need opposite responses. Treating a parse failure as "start from {}"
    # would rewrite the file with only these entries, discarding every permission already in it --
    # and a truncated file is reachable, because the editor writes this file too whenever a prompt
    # is answered with "always allow".
    data = {}
    if LOCAL_SETTINGS.exists():
        try:
            data = json.loads(LOCAL_SETTINGS.read_text())
        except ValueError as e:
            sys.exit(f"error: {LOCAL_SETTINGS} exists but is not valid JSON ({e}).\n"
                     "Refusing to overwrite it. Fix or move it, then re-run.")
    perms = data.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    allow.extend(missing)
    LOCAL_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    trainlib.write_json_atomic(LOCAL_SETTINGS, data)
    print(f"\nWROTE           {len(missing)} entries to {LOCAL_SETTINGS}")
    print("                restart the session (or /permissions reload) to pick them up")


def cmd_days(args) -> None:
    data = load()
    # Only a date persists anything; the bare form just reports, so it can tolerate the placeholder.
    rel = args.release or current_release(required=bool(args.date))
    b = bucket(data, rel)
    if args.date:
        b["days_reviewed"] = sorted(set(b["days_reviewed"]) | {args.date})
        save(data)
        print(f"[{rel}] recorded {args.date} as reviewed")
    days = b["days_reviewed"]
    print(f"[{rel}] {len(days)} day(s) reviewed: {', '.join(days) or 'none'}")


def render(rel: str, b: dict, show_all: bool, verbose: bool) -> None:
    items = b["items"]
    rows = [(k, v) for k, v in items.items() if show_all or v.get("status") not in CLOSED]
    rows.sort(key=lambda kv: (kv[1].get("status", ""), kv[0]))
    days = b.get("days_reviewed", [])
    print(f"== Firefox {rel} — {len(rows)} open of {len(items)} tracked"
          + (f", {len(days)} day(s) reviewed" if days else ""))
    for key, it in rows:
        print(f"  {key:<12} [{it.get('status','?')}] {it.get('kind','')}")
        if it.get("due"):
            print(f"      follow up after {it['due']}")
        if it.get("summary"):
            print(f"      {it['summary']}")
        if verbose:
            for e in it.get("log", [])[-6:]:
                print(f"        {e['date']}  {e['text']}")
    if days:
        print(f"  days reviewed: {', '.join(days)}")


def cmd_list(args) -> None:
    data = load()
    if not data["releases"]:
        print(f"Watchlist is empty ({WATCHLIST_FILE})")
        return
    print(f"{WATCHLIST_FILE}\n")
    rels = sorted(data["releases"], key=lambda r: (not r.isdigit(), r))
    if not args.all_releases:
        rel = args.release or current_release()
        if rel not in data["releases"]:
            print(f"Nothing tracked for Firefox {rel}. Releases with state: {', '.join(rels)}")
            return
        rels = [rel]
    for rel in rels:
        render(rel, data["releases"][rel], args.all, args.verbose)
        print()


def cmd_summary(args) -> None:
    data = load()
    print(f"{WATCHLIST_FILE}\n")
    for rel in sorted(data["releases"], key=lambda r: (not r.isdigit(), r)):
        b = data["releases"][rel]
        counts: dict[str, int] = {}
        for it in b["items"].values():
            counts[it.get("status", "?")] = counts.get(it.get("status", "?"), 0) + 1
        bits = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
        print(f"  Firefox {rel}: {len(b['items'])} tracked ({bits}); "
              f"{len(b.get('days_reviewed', []))} day(s) reviewed")


def annotate(bug_ids: list[str]) -> dict[str, dict]:
    """For daily-pass: which of these bugs are tracked, in any release?"""
    data = load()
    out = {}
    for rel, b in data["releases"].items():
        for bug in bug_ids:
            if bug in b["items"]:
                out[bug] = {**b["items"][bug], "release": rel}
    return out


def standing(exclude: set[str]) -> dict[str, dict]:
    """Open tracked items not in the current window, across all releases."""
    data = load()
    out = {}
    for rel, b in data["releases"].items():
        for k, v in b["items"].items():
            if k not in exclude and v.get("status") not in CLOSED:
                out[k] = {**v, "release": rel}
    return out


def cmd_followup(args) -> None:
    """Where does each ask actually stand?

    Three states, and the distinction matters. A bug is only "needs chasing" when nobody
    owes us anything: no needinfo outstanding *and* no relnote flag. A reply in the
    comments is not the same as an answer -- on bug 2051354 an affected user replied
    helpfully while the needinfo on the assignee stayed open, so it is still pending, not
    stranded.
    """
    import urllib.parse as up
    data = load()
    rel = args.release or current_release()
    pending = []
    for r, b in data["releases"].items():
        if args.all_releases or r == rel:
            for k, v in b["items"].items():
                if k.isdigit() and v.get("status") in ("asked", "replied"):
                    pending.append((r, k, v))
    if not pending:
        print("Nothing awaiting follow-up.")
        return
    ids = [k for _, k, _ in pending]
    qs = up.urlencode({"id": ",".join(ids),
                       "include_fields": "id,cf_tracking_firefox_relnote,summary,flags"})
    try:
        bugs = {str(b["id"]): b for b in
                trainlib.fetch_json(f"https://bugzilla.mozilla.org/rest/bug?{qs}").get("bugs", [])}
    except RuntimeError as e:
        sys.exit(f"error: could not reach Bugzilla: {e}")

    nominated, waiting, chase = [], [], []
    for r, k, v in sorted(pending, key=lambda t: t[1]):
        bug = bugs.get(k, {})
        flag = bug.get("cf_tracking_firefox_relnote") or "---"
        nis = [f for f in (bug.get("flags") or [])
               if f.get("name") == "needinfo" and f.get("status") == "?"]
        row = (r, k, v, flag, nis, bug.get("summary", ""))
        if flag != "---":
            nominated.append(row)
        elif nis:
            waiting.append(row)
        else:
            chase.append(row)

    print("NOMINATED (relnote flag set -- in the process, nothing to do):")
    for r, k, v, flag, _nis, summ in nominated:
        print(f"  Fx{r} {k}: {flag:<8} {summ[:62]}")
    print("  none" if not nominated else "")

    print("AWAITING REPLY (needinfo still open -- someone owes us an answer):")
    for r, k, v, _f, nis, summ in waiting:
        who = ", ".join(f.get("requestee", "?") for f in nis)
        due = f"  [follow up after {v['due']}]" if v.get("due") else ""
        print(f"  Fx{r} {k}: ni on {who}{due}")
        print(f"      {summ[:74]}")
    print("  none" if not waiting else "")

    print("NEEDS CHASING (no needinfo, no relnote flag -- nobody owes us anything):")
    for r, k, v, _f, _nis, summ in chase:
        due = f"  [follow up after {v['due']}]" if v.get("due") else ""
        print(f"  Fx{r} {k}: [{v['status']}]{due} {summ[:62]}")
    print("  none" if not chase else "")


def cmd_replies(args) -> None:
    """New replies on asked/replied bugs, since our ask.

    Replaces looping curl over each bug by hand -- the same reason bug-detail.py exists:
    a stable script is allowlisted once, ad-hoc shell prompts every time.
    """
    data = load()
    rel = args.release or current_release()
    targets = []
    for r, b in data["releases"].items():
        if args.all_releases or r == rel:
            for k, v in b["items"].items():
                if k.isdigit() and v.get("status") in ("asked", "replied", "watching"):
                    targets.append((r, k, v))
    if not targets:
        print("Nothing to check.")
        return
    for r, k, v in sorted(targets, key=lambda t: t[1]):
        try:
            payload = trainlib.fetch_json(
                f"https://bugzilla.mozilla.org/rest/bug/{k}/comment")
        except RuntimeError as e:
            print(f"  Fx{r} {k}: could not fetch ({e})")
            continue
        cs = []
        for vv in (payload.get("bugs") or {}).values():
            cs = vv.get("comments", [])
        ours = [i for i, c in enumerate(cs) if "relnote" in c.get("text", "").lower()
                and c.get("creator", "").startswith("ryanvm")]
        after = cs[ours[-1] + 1:] if ours else []
        if not after:
            continue
        print(f"=== Fx{r} bug {k}  [{v.get('status')}]  {len(after)} repl(y/ies) since the ask")
        for c in after[-2:]:
            body = " ".join(c["text"].split())
            print(f"    #{c['count']} {c['creator']}: {body[:400]}")
        print()


def main() -> None:
    p = argparse.ArgumentParser(description="Release-note watchlist, organised by release.")
    p.add_argument("--release", default=None, help="Firefox version (default: current Nightly)")

    # `--release` reads naturally after the subcommand (`add 2055710 --release 155`), and every
    # example written by hand put it there, so accept both positions. `SUPPRESS` is what makes that
    # safe: a normal subparser default would overwrite the attribute the top-level flag already set,
    # silently discarding `watchlist.py --release 155 add ...`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--release", default=argparse.SUPPRESS,
                        help="Firefox version (default: current Nightly); may also precede the "
                             "subcommand")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add_parser(name: str, **kw):
        return sub.add_parser(name, parents=[common], **kw)

    a = add_parser("add")
    a.add_argument("key")
    a.add_argument("--kind", choices=["bug", "feature", "decision"], default=None)
    a.add_argument("--status", choices=STATUSES, default=None)
    a.add_argument("--note", default=None)
    a.add_argument("--due", default=None,
                   help="YYYY-MM-DD to follow up after, for commitments like 'I'll revisit "
                        "next week' that are easy to forget")
    a.set_defaults(func=cmd_add)

    n = add_parser("note")
    n.add_argument("key")
    n.add_argument("text")
    n.set_defaults(func=cmd_note)

    for name, st in (("done", "done"), ("decline", "declined"), ("asked", "asked"),
                     ("replied", "replied"), ("noted", "noted")):
        sp = add_parser(name, help=f"mark as {st}")
        sp.add_argument("key")
        sp.set_defaults(func=lambda args, _st=st: set_status(args, _st))

    r = add_parser("rm")
    r.add_argument("key")
    r.set_defaults(func=cmd_rm)

    d = add_parser("days", help="record/show reviewed Nightly days for a release")
    d.add_argument("date", nargs="?", default=None, help="YYYYMMDD")
    d.set_defaults(func=cmd_days)

    lg = add_parser("log", help="append/show release-level context notes")
    lg.add_argument("text", nargs="?", default=None)
    lg.set_defaults(func=cmd_log)

    rs = add_parser("resume", help="briefing for a fresh or post-compaction session")
    rs.add_argument("--repo", default=None)
    rs.set_defaults(func=cmd_resume)

    cs = add_parser("check-setup",
                        help="locate/save the Gecko checkout and report missing permissions")
    cs.add_argument("--repo", default=None,
                    help="path to the Gecko clone; saves it for every script to use")
    cs.add_argument("--write", action="store_true",
                    help="write the missing permission entries to .claude/settings.local.json")
    cs.set_defaults(func=cmd_check_setup)

    lst = add_parser("list")
    lst.add_argument("--all", action="store_true", help="include done/declined/noted")
    lst.add_argument("--all-releases", action="store_true")
    lst.add_argument("-v", "--verbose", action="store_true")
    lst.set_defaults(func=cmd_list)

    fu = add_parser("followup", help="check relnote flag state on asked/replied bugs")
    fu.add_argument("--all-releases", action="store_true")
    fu.set_defaults(func=cmd_followup)

    rp = add_parser("replies", help="show replies received since each ask")
    rp.add_argument("--all-releases", action="store_true")
    rp.set_defaults(func=cmd_replies)

    s = add_parser("summary")
    s.set_defaults(func=cmd_summary)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
