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
  watchlist.py add 2051691 --status gated --gate browser.referrals.enabled
  watchlist.py gates                     # re-check every recorded gate
  watchlist.py carry 2051691 css-line-clamp --from 155
  watchlist.py drop-release 155          # refuses while 155 has open entries
"""

import argparse
import json
import re
import subprocess
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


PREF_DELTA = Path(__file__).resolve().parent / "pref-delta.py"


def resolve_prefs(names: list[str], fetch: bool = True,
                  repo: Path | None = None) -> dict[str, dict]:
    """pref-delta's --lookup verdict per name, keyed by name. Raises RuntimeError if it fails.

    Through pref-delta rather than a second parser, so a recorded gate and a `--lookup` by hand
    can never disagree about what the same preference defaults to.
    """
    prefs = [n for n in names if not n.startswith("fml:")]
    fmls = [n[4:] for n in names if n.startswith("fml:")]
    cmd = [sys.executable, str(PREF_DELTA), "--format", "json"]
    if prefs:
        cmd += ["--lookup", ",".join(prefs)]
    if fmls:
        cmd += ["--fml", ",".join(fmls)]
    if not fetch:
        cmd.append("--no-fetch")
    if repo:
        cmd += ["--repo", str(repo)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["no output"])[-1]
        raise RuntimeError(f"pref-delta exited {r.returncode}: {tail}")
    # pref-delta exits 0 after a failed fetch, and that warning is the only sign the defaults
    # below describe a stale tree.
    for ln in r.stderr.splitlines():
        if "WARNING: git fetch" in ln:
            print(ln, file=sys.stderr)
    return {res["pref"]: res for res in json.loads(r.stdout)}


def gate_record(res: dict) -> dict:
    return {"state": res["summary"], "values": res["values"], "recorded": now(),
            "guess": res.get("unresolved_define") or []}


def gate_caveat(rec: dict) -> str:
    bits = []
    if rec.get("guess"):
        bits.append(f"GUESS: behind {', '.join(rec['guess'])}")
    blind = sorted({k.split("/", 1)[1] for k, v in rec["values"].items() if v == "<complex>"})
    if blind:
        bits.append(f"NOT COMPARED: {', '.join(blind)} (map/list values; changes there go unseen)")
    return "".join(f"  ({b})" for b in bits)


def release_on(values: dict) -> bool:
    return any(v == "true" for k, v in values.items() if k.startswith("release/"))


def check_gates(data: dict, fetch: bool = True, repo: Path | None = None) -> dict:
    """Re-resolve every gate recorded on an open item and compare it with what was recorded.

    Deliberately never updates the stored baseline: a change keeps being reported on every pass
    until someone acts on it (re-records the gate with `add --gate`, or closes the item). A report
    that quietly absorbed the change would show a flip exactly once, to whichever pass happened to
    run first, and in the 155 state 11 of 36 open entries had flipped unnoticed across three cycles
    while every daily report kept listing them as gated.
    """
    rows = [(rel, key, pref, rec)
            for rel, b in data["releases"].items()
            for key, it in b["items"].items() if it.get("status") not in CLOSED
            for pref, rec in (it.get("gates") or {}).items()]
    out = {"changed": [], "gone": [], "unresolvable": [], "same": [], "error": None}
    if not rows:
        return out
    try:
        now_state = resolve_prefs(sorted({r[2] for r in rows}), fetch=fetch, repo=repo)
    except RuntimeError as e:
        out["error"] = str(e)
        return out
    for rel, key, pref, rec in rows:
        res = now_state.get(pref, {"found": False, "written_at": []})
        row = {"release": rel, "key": key, "pref": pref, "was": rec["state"],
               "recorded": rec.get("recorded", "?")}
        if not res["found"]:
            out["unresolvable" if res.get("written_at") else "gone"].append(
                {**row, "last_change": res.get("last_change", "")})
        elif res["values"] != rec["values"]:
            out["changed"].append({**row, "now": res["summary"],
                                   "release_on": release_on(res["values"])
                                   and not release_on(rec["values"])})
        else:
            out["same"].append(row)
    return out


def print_gate_report(rep: dict) -> None:
    """Shared by `gates` and daily-pass, so the two cannot describe the same result differently."""
    if rep["error"]:
        print(f"GATE RE-CHECK FAILED -- {rep['error']}. This is not 'no gate changes'; the recorded "
              "gates were never compared.")
        return
    total = sum(len(rep[k]) for k in ("changed", "gone", "unresolvable", "same"))
    if not total:
        print("No open item carries a recorded gate (add one with `add <key> --gate <pref>`).")
        return
    for row in rep["changed"]:
        loud = "  *** NOW ON FOR RELEASE ***" if row["release_on"] else ""
        print(f"  CHANGED  Fx{row['release']} {row['key']}: {row['pref']}{loud}")
        print(f"           was ({row['recorded']}): {row['was']}")
        print(f"           now: {row['now']}")
    for row in rep["gone"]:
        where = (f"last touched by {row['last_change']}" if row.get("last_change")
                 else "find which with git log -S on the name")
        print(f"  GONE     Fx{row['release']} {row['key']}: {row['pref']} is no longer in the tree. "
              "The gate was removed, so the feature has shipped unconditionally or been taken "
              f"out; {where}.")
    for row in rep["unresolvable"]:
        print(f"  UNKNOWN  Fx{row['release']} {row['key']}: {row['pref']} is in the tree but "
              "pref-delta computed no default for it; run --lookup on it to see why.")
    print(f"  {len(rep['same'])} of {total} recorded gate(s) unchanged. A reported gate repeats "
          "until acted on: `add <key> --gate <pref>` re-records a changed one, `--drop-gate "
          "<pref>` retires a gone or unknown one, and closing the item stops both.")


def cmd_gates(args) -> None:
    print_gate_report(check_gates(load(), fetch=not args.no_fetch))


def cmd_add(args) -> None:
    data = load()
    key = str(args.key)
    # A gate change is acted on where the entry lives: filing it under the current Nightly would
    # create a second entry and leave the reported one repeating its CHANGED line forever.
    hit = find(data, key, None) if (args.gate or args.drop_gate) and not args.release else None
    rel = hit[0] if hit else (args.release or current_release(required=True))
    gates = {}
    if args.gate:
        # Resolved before anything is written: a misspelt name has to fail here, where the person
        # recording it is looking, not surface weeks later as a GONE on a gate that never existed.
        # dom.scoped-custom-element-registries.enabled is hyphenated and its C++ accessor is not.
        try:
            found = resolve_prefs(args.gate, fetch=not args.no_fetch)
        except RuntimeError as e:
            sys.exit(f"error: could not resolve --gate: {e}")
        for pref in args.gate:
            res = found.get(pref, {"found": False})
            if not res["found"]:
                hint = (f" Nearest names: {', '.join(res['near'])}." if res.get("near") else "")
                probe = (f"--fml {pref[4:]}" if pref.startswith("fml:") else f"--lookup {pref}")
                sys.exit(f"error: --gate {pref}: pref-delta has no default for it at "
                         f"{trainlib.gecko_upstream()}, so there is nothing to watch.{hint} "
                         f"Run pref-delta.py {probe} for the detail. Nothing was recorded.")
            gates[pref] = gate_record(res)
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
    # Same reason as in set_status: record the ask date once, so re-recording an item later to
    # revise its note cannot narrow the reply window.
    if item["status"] in ("asked", "replied"):
        item.setdefault("asked_on", item.get("added") or now())
    if args.note:
        item["log"].append({"date": now(), "text": args.note})
    if gates:
        item.setdefault("gates", {}).update(gates)
        item["log"].append({"date": now(), "text": "gate recorded: " + "; ".join(
            f"{p} = {g['state']}{gate_caveat(g)}" for p, g in gates.items())})
    for pref in args.drop_gate or []:
        if pref not in item.get("gates", {}):
            sys.exit(f"error: --drop-gate {pref}: {key} has no such recorded gate. Nothing was "
                     "recorded.")
        was = item["gates"].pop(pref)["state"]
        item["log"].append({"date": now(), "text": f"gate dropped: {pref} (was {was})"})
    b["items"][key] = item
    save(data)
    print(f"[{rel}] {key}: {item['status']}")
    for pref, g in gates.items():
        print(f"    gate {pref}: {g['state']}{gate_caveat(g)}")


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
        # Name the recovery: a transition on something never added is the common first move for an
        # item the pass has only just decided about, and `add` both creates and sets the status.
        rel_hint = f" --release {args.release}" if args.release else ""
        sys.exit(f"error: {args.key} is not tracked, so there is no status to change. To record it "
                 f"now:\n  watchlist.py{rel_hint} add {args.key} --status {status} "
                 f"--note \"<why>\"")
    rel, item = hit
    item["status"] = status
    item["updated"] = now()
    # --note here rather than only on `add`: the reason a thing was declined is the part worth
    # keeping, and without it the short form silently drops it. `add --status declined --note ...`
    # works but overwrites the item's summary with the reason, which is a different record.
    # First ask wins, and later transitions must not move it: `replies` uses this as the cutoff for
    # "what has been said since", and a cutoff that drifts forward hides the replies it exists to
    # find. Derived from the log instead, bug 1699444's window started three days after the ask and
    # skipped a 2,400-character objection from the developer.
    if status in ("asked", "replied"):
        item.setdefault("asked_on", item.get("added") or now())
    note = getattr(args, "note", None)
    text = f"status -> {status}" + (f": {note}" if note else "")
    item.setdefault("log", []).append({"date": now(), "text": text})
    save(data)
    print(f"[{rel}] {args.key} -> {status}" + (" (reason logged)" if note else ""))


def cmd_rm(args) -> None:
    data = load()
    hit = find(data, str(args.key), args.release)
    if not hit:
        sys.exit(f"error: {args.key} is not tracked")
    rel, _ = hit
    data["releases"][rel]["items"].pop(str(args.key))
    save(data)
    print(f"[{rel}] removed {args.key}")


def cmd_carry(args) -> None:
    """Move open entries from an older release into this one, keeping their whole log.

    What makes a cycle's state safe to drop: the entries still worth watching come forward first.
    All keys are checked before any moves, so a typo leaves both releases untouched.
    """
    data = load()
    args.keys = list(dict.fromkeys(args.keys))
    src = str(args.from_release)
    dst = args.release or current_release(required=True)
    if src == dst:
        sys.exit(f"error: --from {src} is the destination release")
    have = data["releases"].get(src, {}).get("items", {})
    missing = [k for k in args.keys if k not in have]
    if missing:
        sys.exit(f"error: not tracked in Firefox {src}: {', '.join(missing)}. Nothing was moved.")
    taken = [k for k in args.keys if k in data["releases"].get(dst, {}).get("items", {})]
    if taken:
        sys.exit(f"error: already tracked in Firefox {dst}: {', '.join(taken)}. Nothing was moved.")
    b = bucket(data, dst)
    gated = 0
    for k in args.keys:
        item = have.pop(k)
        if "target" in item:
            item["target"] = dst
        item["updated"] = now()
        item.setdefault("log", []).append(
            {"date": now(), "text": f"carried from Fx{src}" + (f": {args.note}" if args.note else "")})
        b["items"][k] = item
        gated += bool(item.get("gates"))
    save(data)
    print(f"[{dst}] carried {len(args.keys)} from Fx{src}: {', '.join(args.keys)}")
    if gated:
        print(f"    with a recorded gate: {gated}; `watchlist.py gates` re-checks them")


def cmd_drop_release(args) -> None:
    """Delete a release's whole state: items, reviewed days and log.

    Refuses while it still holds open entries, because an open entry in an old release is what the
    daily pass re-surfaces, and losing one silently is how a feature ships without a note.
    """
    data = load()
    rel = str(args.release_to_drop)
    b = data["releases"].get(rel)
    if b is None:
        print(f"Firefox {rel} has no state; nothing to drop.")
        return
    cur = current_release()
    if not args.force and rel == cur:
        sys.exit(f"error: Firefox {rel} is the current Nightly. Pass --force to drop it anyway.")
    if not args.force and cur == "unknown":
        sys.exit(f"error: cannot determine the current Nightly, so cannot rule out that it is "
                 f"Firefox {rel}. Pass --force to drop it anyway.")
    open_items = sorted(k for k, v in b["items"].items() if v.get("status") not in CLOSED)
    if open_items and not args.force:
        print(f"error: Firefox {rel} still has {len(open_items)} open item(s):", file=sys.stderr)
        for k in open_items:
            it = b["items"][k]
            gate = "  [gate recorded]" if it.get("gates") else ""
            print(f"  {k:<12} [{it.get('status')}]{gate} {it.get('summary', '')[:80]}",
                  file=sys.stderr)
        sys.exit(f"Carry the ones still worth watching first (watchlist.py carry <key>... --from "
                 f"{rel}), then re-run; --force drops them all.")
    data["releases"].pop(rel)
    save(data)
    print(f"dropped Firefox {rel}: {len(b['items'])} item(s), "
          f"{len(b.get('days_reviewed', []))} reviewed day(s), {len(b.get('log', []))} log entries")


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

    # First, because it is the one line that can invalidate the rest: if the skill driving this
    # pass is behind the one on origin, the answer is to restart before reading any further.
    print_tooling(trainlib.tooling_status(fetch=not args.no_fetch))

    repo = trainlib.resolve_repo(args.repo)
    st = trainlib.watermark_status(repo, trainlib.read_watermark(),
                                   int(rel) if rel.isdigit() else 0)
    if st.get("known"):
        stale = "  *** STALE: predates the current train ***" if st.get("stale_train") else ""
        print(f"SCAN POSITION  watermark {st['commit'][:12]} ({st['date']}), "
              f"{st['commits_behind']} commits behind {st.get('upstream', 'upstream')}{stale}")
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
                         "for-each-ref", "tag", "branch", "fetch", "ls-tree", "cat-file")
REPO_ROOT = trainlib.RELMAN_ROOT
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


def print_tooling(st: dict, label: str = "TOOLING", pad: int = 15,
                  stale: list[str] | None = None, pulled: bool = False,
                  pull_attempted: bool = False) -> None:
    """The tooling verdict as a labelled block, continuation lines aligned under the first.

    The banner deliberately breaks that alignment and prints flush left: it is not a detail of the
    TOOLING line, it is a stop sign for the whole pass.
    """
    # One value to both: they agree today only because the banner reads it solely on the not-pulled
    # path, which is a coincidence an edit could break.
    failed_pull = pull_attempted and not pulled
    lines = trainlib.tooling_summary(st, pull_attempted=failed_pull)
    print(f"{label.ljust(pad)}{lines[0]}")
    for extra in lines[1:]:
        print(f"{''.ljust(pad)}{extra}")
    banner = trainlib.tooling_banner(st, stale=stale, pulled=pulled,
                                     pull_attempted=failed_pull)
    if banner:
        print()
        print("\n".join(banner))
        print()


def cmd_check_updates(args) -> None:
    """Is the release-note tooling in this checkout behind origin, and does that need a restart?

    Distinct from check-setup, which is once per machine. This is once per pass: as soon as more
    than one person is editing the skill, the copy driving a pass can be days behind the one its
    author is describing, and nothing in the run or its transcript would say so.

    `--pull` fast-forwards the checkout, under the conditions in trainlib.pull_blocker. Bringing
    the tooling up to date is the point of noticing it is behind, and leaving it as an instruction
    for a human is how it gets skipped on the pass that needed it.

    Exit 1 means one thing: the skills are stale and this session must `/clear` (matching
    doc-flag-audit.py's "non-zero means act"). That holds whether or not the pull succeeded, since
    a pull leaves the new text on disk but not in this conversation.

    A pull that does *not* land is loud but not fatal, so it does not change the exit code: the
    older scripts still work, and the pass is better off continuing on them than refusing to start
    because a laptop was off VPN. What it must not do is read as success, which is why the summary
    says the pull did not happen rather than suggesting it again.
    """
    st = trainlib.tooling_status(fetch=not args.no_fetch)
    # The revision this session started on, kept whole. A successful pull rebinds `st` to the new
    # one, and every question below -- did the skills change, which docs moved, was any of it even
    # classifiable -- is about the transition rather than the state left behind it. Held as one
    # object rather than a field at a time: that list grew by one on each of the last two passes
    # over this function, which is the shape being wrong rather than a field being forgotten.
    before = st
    stale = list(before.get("needs_clear") or [])
    pulled = attempted = False
    if args.pull and st.get("behind"):
        attempted = True
        result = trainlib.pull_tooling(st)
        pulled = result["pulled"]
        print(result["message"])
        if pulled:
            # Re-read rather than patch: HEAD moved, so every count in the old status is stale.
            st = trainlib.tooling_status(fetch=False)
            if not before.get("changed_known", True):
                # The pull went through but its contents were never classifiable, so the new status
                # reads "current" and cannot say whether a skill moved. Not a stop -- the same
                # loud-but-open call as a failed pull -- but it must not pass in silence.
                print("...though the file list could not be read beforehand, so whether the skills "
                      "were among the changes is unknown. Carry on; /clear if this pass reads oddly")
            docs = before.get("changed_docs") or []
            if docs:
                # Re-reading is enough here -- no /clear -- but it has to actually happen. Anything
                # already read into this conversation is now the old text.
                print("re-read before relying on them, a copy already in this conversation is now "
                      "stale: " + ", ".join(docs))
    # --quiet exists for a check that runs at the top of every pass: say nothing when there is
    # nothing to act on. Anything unresolved -- no checkout, no upstream ref, a failed fetch --
    # still speaks, because a check that could not run must not look like one that came back clean.
    if (args.quiet and not stale and st.get("available") and st.get("upstream_known")
            and st.get("fetched") is not False
            and not st.get("behind") and not st.get("dirty")):
        return
    print_tooling(st, pad=9, stale=stale, pulled=pulled, pull_attempted=attempted)
    if stale:
        sys.exit(1)


def save_upstream(repo: Path) -> None:
    """Work out which ref tracks Gecko upstream in `repo`, save it, and say which one it is.

    Locating the clone is not enough on its own. Every window count and every preference default is
    read from this ref, and the remote that carries it is only called `origin` by convention -- the
    more common GitHub layout puts your own fork there. Under that layout nothing fails: the fetch
    succeeds, `git show <ref>:<file>` succeeds, and the defaults are simply a fork's, which on one
    real clone meant 13,947 commits of drift with nothing on screen saying so. So it is resolved
    here, once, and recorded next to the path.
    """
    ref, why = trainlib.detect_gecko_upstream(repo)
    if not ref:
        sys.exit(f"error: cannot tell which ref tracks Gecko upstream in {repo} -- {why}.\n"
                 "Add a remote for the canonical repository and fetch it, then re-run. Every\n"
                 "preference default and window count is read from that ref, so guessing here\n"
                 "would produce confidently wrong answers rather than an error.")
    path = trainlib.write_config(gecko_upstream=ref)
    print(f"GECKO UPSTREAM  {ref}  ({why})\n                saved to {path}")


def cmd_check_setup(args) -> None:
    """Resolve the Gecko checkout once, save it, and report the permission entries it needs.

    The clone lives somewhere different on every machine, so neither the scripts nor the
    shared settings.json can hardcode it. This is the one place that learns the path -- and the
    upstream ref, which is per-machine for the same reason. See save_upstream.
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

    # Re-detected on every run rather than only when writing the path: a remote added or renamed
    # after setup would otherwise leave the stored ref pointing at something that no longer tracks
    # upstream, and this is the only command anyone re-runs to check their setup.
    save_upstream(repo)

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


def render(rel: str, b: dict, show_all: bool, verbose: bool, status: str = None) -> None:
    items = b["items"]
    if status:
        # An explicit status implies wanting it whatever it is, so this ignores show_all: `declined`
        # is a closed status and asking for it should not also require --all.
        rows = [(k, v) for k, v in items.items() if v.get("status") == status]
    else:
        rows = [(k, v) for k, v in items.items() if show_all or v.get("status") not in CLOSED]
    rows.sort(key=lambda kv: (kv[1].get("status", ""), kv[0]))
    days = b.get("days_reviewed", [])
    if status:
        print(f"== Firefox {rel} — {len(rows)} with status '{status}' of {len(items)} tracked"
              + (f", {len(days)} day(s) reviewed" if days else ""))
        if not rows:
            # Say which statuses exist rather than printing nothing: an empty list from a typo and an
            # empty list from a genuinely empty bucket look identical.
            present = sorted({v.get("status", "?") for v in items.values()})
            print(f"   none. Statuses in use for this release: {', '.join(present)}")
    else:
        print(f"== Firefox {rel} — {len(rows)} open of {len(items)} tracked"
              + (f", {len(days)} day(s) reviewed" if days else ""))
    for key, it in rows:
        print(f"  {key:<12} [{it.get('status','?')}] {it.get('kind','')}")
        if it.get("due"):
            print(f"      follow up after {it['due']}")
        if it.get("summary"):
            print(f"      {it['summary']}")
        # For a closed item the summary describes the bug, not the outcome, so surface the last log
        # line too -- that is where `decline --note "<why>"` puts the reason, and needing --verbose
        # to see why something was declined makes the reason easy to lose.
        if not verbose and it.get("status") in CLOSED and it.get("log"):
            # The most recent *status* entry, not the most recent entry: a plain `note` added after
            # the decision would otherwise displace the reason for it.
            last = next((e for e in reversed(it["log"])
                         if e.get("text", "").startswith("status -> ")), it["log"][-1])
            # Items closed with `add --status declined --note ...` stored the same text as both the
            # summary and the log entry; printing it twice is worse than not printing it at all.
            if last.get("text") != it.get("summary"):
                print(f"      {last['date']}  {last['text']}")
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
        render(rel, data["releases"][rel], args.all, args.verbose, args.status)
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


# `bmo.tld` is a reserved fake domain BMO gives its service accounts -- pulsebot, phab-bot and the
# rest -- so no person has an address there. Naming the bots individually missed phab-bot's uplift
# mail, which then reported itself as a comment "from people".
BOT_CREATOR = re.compile(r"@bmo\.tld|@bots\.|pulsebot|bugzilla-daemon", re.IGNORECASE)


def automated(comment: dict) -> bool:
    """Whether a comment is machine-generated rather than someone answering.

    Push notices, attachment mail and bare revision URLs are the bulk of a busy bug's comment
    stream and none of them is a reply. Matched on the author where possible and on the body's
    opening otherwise, because a push can be attributed to the developer who landed it.
    """
    body = " ".join((comment.get("text") or "").split())
    return (bool(BOT_CREATOR.search(comment.get("creator", "")))
            or body.startswith("Created attachment")
            or body.startswith(("https://hg.mozilla.org", "https://github.com")))


def cmd_replies(args) -> None:
    """New comments on asked/replied/watching bugs since we asked.

    Replaces looping curl over each bug by hand -- the same reason bug-detail.py exists:
    a stable script is allowlisted once, ad-hoc shell prompts every time.

    The cutoff comes from *our own record* of when the item was asked, not from trying to
    recognise our own comments on the bug. Recognising them meant matching a hardcoded Bugzilla
    login, which worked for exactly one person and, when it failed, failed silently: no ask found
    meant every bug was skipped and the command printed nothing, which reads identically to
    "nothing to chase". The date is day-granular, so a same-day ask can appear in its own results
    -- over-inclusion, which is the safe direction for a check meant to catch things.
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
    checked = 0
    with_replies = 0
    own_only = 0
    bots_only = 0
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
        # Recorded at the ask if we have it, else when the item was added -- an item cannot have
        # been asked before it existed, so `added` is never *late*. Never `updated`: any later edit
        # bumps it, which silently moves the window past the replies it exists to find. Erring early
        # costs noise; erring late costs the answer. An empty cutoff would include everything, which
        # is the harmless direction.
        since = v.get("asked_on") or v.get("added") or ""
        checked += 1
        after = [c for c in cs if (c.get("creation_time") or "")[:10] >= since]
        if not after:
            continue
        # Show what people said; count everything. Push notices and attachment mail are volume, not
        # answers, and with only two lines shown they crowded the answers out: on bug 2056931 both
        # displayed comments were a pulsebot push and an attachment notice while the one human
        # comment in the window stayed invisible.
        human = [c for c in after if not automated(c)]
        if not human:
            bots_only += 1
            continue
        # For an asked item, a single human comment dated the ask day is the ask itself -- the
        # day-granular cutoff cannot exclude it by time. Watched items were never asked, so the
        # rule does not apply to them.
        if (v.get("status") in ("asked", "replied") and len(human) == 1
                and (human[0].get("creation_time") or "")[:10] == since):
            own_only += 1
            continue
        with_replies += 1
        extra = f", {len(human)} from people" if len(human) != len(after) else ""
        print(f"=== Fx{r} bug {k}  [{v.get('status')}]  {len(after)} comment(s) since {since}"
              f"{extra}")
        for c in human[-3:]:
            print(f"    #{c['count']} {c['creator']}: {trainlib.preview(c['text'], 600)}")
        print()
    # Always say what was covered, including what was held back: an empty run has to be
    # distinguishable from a broken one, and a suppressed item from an absent one.
    print(f"checked {checked} item(s) for comments since the ask (or since added, for watched "
          f"items); {with_replies} with replies"
          + (f"; {own_only} with only our own ask" if own_only else "")
          + (f"; {bots_only} with only automated updates" if bots_only else ""))


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
    a.add_argument("--gate", action="append", default=None, metavar="PREF",
                   help="preference that keeps this off, or fml:<feature> for a Fenix Nimbus "
                        "feature; its current defaults are recorded and "
                        "re-checked by `gates` and every daily-pass. Repeatable; re-recording one "
                        "replaces its baseline")
    a.add_argument("--drop-gate", action="append", default=None, metavar="PREF",
                   help="stop watching a recorded gate, e.g. one `gates` reports as GONE or "
                        "UNKNOWN, which cannot be re-recorded")
    a.add_argument("--no-fetch", action="store_true",
                   help="resolve --gate without fetching the Gecko clone first")
    a.set_defaults(func=cmd_add)

    g = add_parser("gates", help="re-check the preference recorded on every open item with --gate")
    g.add_argument("--no-fetch", action="store_true",
                   help="resolve from the Gecko clone as-is, without fetching first")
    g.set_defaults(func=cmd_gates)

    c = add_parser("carry", help="move open entries from an older release into this one, log intact")
    c.add_argument("keys", nargs="+")
    c.add_argument("--from", dest="from_release", required=True, help="release to take them from")
    c.add_argument("--note", default=None, help="appended to each carried entry's log")
    c.set_defaults(func=cmd_carry)

    dr = add_parser("drop-release",
                    help="delete a release's whole state; refuses while it has open entries")
    dr.add_argument("release_to_drop", metavar="release")
    dr.add_argument("--force", action="store_true",
                    help="drop open entries too, or the current Nightly")
    dr.set_defaults(func=cmd_drop_release)

    n = add_parser("note")
    n.add_argument("key")
    n.add_argument("text")
    n.set_defaults(func=cmd_note)

    for name, st in (("done", "done"), ("decline", "declined"), ("asked", "asked"),
                     ("replied", "replied"), ("noted", "noted")):
        sp = add_parser(name, help=f"mark as {st}")
        sp.add_argument("key")
        sp.add_argument("--note", default=None,
                        help="why -- appended to the item's log with the status change")
        sp.set_defaults(func=lambda args, _st=st: set_status(args, _st))

    r = add_parser("rm", help="delete an entry outright -- prefer a status change, which keeps "
                              "the note trail")
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
    rs.add_argument("--no-fetch", action="store_true",
                    help="skip the origin fetch behind the TOOLING line (offline runs)")
    rs.set_defaults(func=cmd_resume)

    cu = add_parser("check-updates",
                    help="is this checkout's release-note tooling behind origin? exits 1 if the "
                         "skills changed and the session needs /clear")
    cu.add_argument("--pull", action="store_true",
                    help="fast-forward to origin/main when behind; refuses unless the checkout is "
                         "on main, clean, and can fast-forward")
    cu.add_argument("--no-fetch", action="store_true",
                    help="compare against the mirror as-is, without fetching first")
    cu.add_argument("--quiet", action="store_true",
                    help="print nothing when the tooling is current and clean, for a check that "
                         "only speaks up when there is something to do")
    cu.set_defaults(func=cmd_check_updates)

    cs = add_parser("check-setup",
                        help="locate/save the Gecko checkout and report missing permissions")
    cs.add_argument("--repo", default=None,
                    help="path to the Gecko clone; saves it for every script to use")
    cs.add_argument("--write", action="store_true",
                    help="write the missing permission entries to .claude/settings.local.json")
    cs.set_defaults(func=cmd_check_setup)

    lst = add_parser("list")
    lst.add_argument("--all", action="store_true", help="include done/declined/noted")
    lst.add_argument("--status", choices=STATUSES, default=None,
                    help="only items with this status. Implies --all, so `--status declined` needs "
                         "nothing else")
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
