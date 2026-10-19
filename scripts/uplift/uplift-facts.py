#!/usr/bin/env python3
"""The mechanical facts the uplift-risk-review skill rates, as JSON, one record per bug.

What needs judgment or Phabricator stays in the skill: reading the uplift diff, deciding whether
churn is a dependency, finding test coverage, and the rating.

Per bug:
  bug          severity, keywords, status flags, regressed_by, regressions (open or not),
               depends_on (and whether each is missing from the branch)
  uplift       the attachments flagged approval-mozilla-<channel>?, each with its Phabricator
               revision and the main revision it was made from; the latest uplift form, parsed
  landings     the bug's commits on main that the branch doesn't have as ancestors, its uplifts
               on the branch, and its uplifts on any newer channel's branch
  pending      the main commits not on the branch yet, oldest first, with per-file line counts
  apply        those commits applied in order to the branch with `git merge-tree`
  churn_before main commits to the same files, before the fix, that the branch doesn't have, each
               marked touches_fix_lines when it changed the lines the fix changes
  churn_after  later commits to the same files on main

Usage:
  uplift-facts.py beta                  # every bug in the dashboard's beta.uplift-requested
  uplift-facts.py esr115 2079560        # just these bugs
  uplift-facts.py release --no-fetch
"""

import argparse
import base64
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "relnotes"))
import trainlib  # noqa: E402

# Bug attribution and backout detection follow scan-window.py, so the two tools agree on what a
# bug's landing is.
_spec = importlib.util.spec_from_file_location("scan_window", ROOT / "scripts/relnotes/scan-window.py")
scan_window = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_window)
BUG_RE, REVERT_RE = scan_window.BUG_RE, scan_window.REVERT_RE

PART_RE = re.compile(r"\bPart\s+(\d+)\b", re.IGNORECASE)
REVERTS_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})")
REVISION_RE = re.compile(r"phabricator\.services\.mozilla\.com/(D\d+)")
FORM_FIELD_RE = re.compile(r"^- \*\*(.+?)\*\*:\s?(.*)$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")
TRAILER_RE = re.compile(r"\s+(r|a|sr)=\S.*$")


def bugs_of(subject: str) -> list[str]:
    """Bugs a commit lands: the ones in its subject before the first " - "."""
    return BUG_RE.findall(subject.split(" - ", 1)[0])


def parse_form(text: str) -> dict:
    """An uplift request comment's `- **Field**: value` lines, continuation lines included."""
    form, key = {}, None
    for line in text.splitlines()[1:]:
        m = FORM_FIELD_RE.match(line)
        if m:
            key = m.group(1).rstrip("?").strip()
            form[key] = m.group(2).strip()
        elif key and line.strip():
            form[key] = f"{form[key]} {line.strip()}".strip()
    return form


def original_revisions(comments: list[dict]) -> dict[int, str]:
    """attachment id -> the main revision it was made from, from the comment that created it."""
    out = {}
    for c in comments:
        m = re.search(r"Created attachment (\d+)", c["text"])
        o = REVISION_RE.search(c["text"].split("Original Revision:", 1)[1]) if "Original Revision:" in c["text"] else None
        if m and o:
            out[int(m.group(1))] = o.group(1)
    return out


def latest_form(comments: list[dict], channel: str) -> dict | None:
    heading = f"### firefox-{channel} Uplift Approval Request"
    forms = [c for c in comments if c["text"].lstrip().startswith(heading)]
    if not forms:
        return None
    return {"comment": forms[-1]["count"], "count": len(forms), **parse_form(forms[-1]["text"].strip())}


def channels() -> list[tuple[str, str]]:
    """(key, version) for beta, release and each ESR, from the dashboard."""
    js = ('const v=await import("./dashboard/site/lib/versions.js"); const r=await v.load(); '
          'console.log(JSON.stringify([r.beta, r.release, ...r.esrs].map(c=>[c.key, String(c.version)])))')
    out = subprocess.run(["node", "--input-type=module", "-e", js], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return [tuple(c) for c in json.loads(out)]


def dashboard_bugs(channel: str) -> list[str]:
    r = subprocess.run(["node", "dashboard/bin/check.js", f"{channel}.uplift-requested", "--json"],
                       cwd=ROOT, capture_output=True, text=True)
    out = json.loads(r.stdout)
    if out.get("state") != "ok":
        sys.exit(f"error: {channel}.uplift-requested: {out.get('message') or out.get('state')}")
    return [str(b["id"]) for b in out["bugs"]]


class Tree:
    def __init__(self, repo: Path, remote: str):
        self.repo, self.remote = repo, remote

    def git(self, *args: str) -> str:
        return trainlib.git(self.repo, *args)

    def ref(self, branch: str) -> str:
        return f"{self.remote}/{branch}"

    def log(self, *args: str) -> list[dict]:
        out = self.git("log", "--format=%H%x1f%cs%x1f%s%x1f%b%x1e", *args)
        commits = []
        for rec in out.split("\x1e"):
            if not rec.strip():
                continue
            sha, date, subject, body = rec.strip("\n").split("\x1f")
            part = PART_RE.search(subject)
            commits.append({"sha": sha[:12], "date": date, "subject": subject,
                            "bugs": bugs_of(subject), "part": int(part.group(1)) if part else None,
                            "revert": bool(REVERT_RE.match(subject)),
                            "reverts": REVERTS_RE.findall(body)})
        return commits

    def landings(self, bug: str, rng: str) -> list[dict]:
        """The bug's own commits in a range, with backouts and what they undid marked.

        Always a range, never a whole branch: --grep over all of history takes seconds per call.
        """
        commits = [c for c in self.log(rng, "-i", f"--grep=bug {bug}") if bug in c["bugs"]]
        undone = {r[:12] for c in commits if c["revert"] for r in c["reverts"]}
        for c in commits:
            c["backed_out"] = c["sha"] in undone
        return commits

    def is_ancestor(self, sha: str, ref: str) -> bool:
        return trainlib.git_rc(self.repo, "merge-base", "--is-ancestor", sha, ref)[0] == 0

    def bugs_in(self, bugs: list[str], rng: str) -> set[str]:
        """Which of these bugs landed a commit in the range."""
        if not bugs:
            return set()
        hits = self.log(rng, "-i", *[f"--grep=bug {b}" for b in bugs])
        return {b for c in hits for b in c["bugs"] if b in bugs}

    def numstat(self, sha: str) -> list[dict]:
        rows = []
        for line in self.git("show", "--numstat", "--format=", sha).splitlines():
            added, removed, path = line.split("\t", 2)
            rows.append({"file": path, "added": added, "removed": removed})
        return rows

    def apply(self, shas: list[str], branch: str) -> dict:
        head = self.git("rev-parse", self.ref(branch)).strip()
        for sha in shas:
            rc, out, err = trainlib.git_rc(self.repo, "merge-tree", "--write-tree",
                                           "--merge-base", f"{sha}^", head, sha)
            if rc != 0:
                files = sorted({ln.split("\t", 1)[1] for ln in out.splitlines()[1:] if "\t" in ln})
                return {"clean": False, "conflict_at": sha, "conflict_files": files,
                        "error": err.strip() or None}
            head = self.git("commit-tree", out.splitlines()[0], "-p", head, "-m", "uplift check").strip()
        return {"clean": True}

    def touching_fix_lines(self, sha: str, since: str) -> set[str]:
        """Commits in since..sha^ that changed the lines `sha` changes, hunk by hunk (`git log -L`)."""
        hits, path = set(), None
        for line in self.git("show", "-U0", "--format=", sha).splitlines():
            if line.startswith("--- "):
                path = line[6:] if line.startswith("--- a/") else None  # None: a new file
            m = HUNK_RE.match(line)
            if m and path:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                end = start + max(count, 1) - 1
                start = max(start, 1)
                rc, out, _ = trainlib.git_rc(self.repo, "log", "--format=%H", "-s",
                                             "-L", f"{start},{end}:{path}", f"{since}..{sha}^")
                hits |= {h[:12] for h in out.split()} if rc == 0 else set()
        return hits

    def uplifted_bugs(self, branch: str) -> set[str]:
        """Bugs with a commit on the branch that isn't on main: its uplifts since the branch was cut."""
        return {b for c in self.log(f"{self.ref('main')}..{self.ref(branch)}") for b in c["bugs"]}


def strip_trailer(subject: str) -> str:
    return TRAILER_RE.sub("", subject).strip()


def facts(bug: str, channel: str, branch: str, newer: list[str], tree: Tree, uplifted: set[str]) -> dict:
    rest = trainlib.BUGZILLA_REST
    fields = "summary,severity,keywords,regressed_by,regressions,depends_on,_custom"
    info = trainlib.fetch_json(f"{rest}/bug/{bug}?include_fields={fields}")["bugs"][0]
    comments = trainlib.fetch_json(f"{rest}/bug/{bug}/comment")["bugs"][bug]["comments"]
    atts = trainlib.fetch_json(f"{rest}/bug/{bug}/attachment?include_fields="
                               "id,summary,content_type,is_obsolete,flags,data")["bugs"][bug]

    linked = info["regressions"] + info["depends_on"]
    linked_info = trainlib.bugzilla_bugs(linked, "id,status,resolution") if linked else {}

    flag = f"approval-mozilla-{channel}"
    originals = original_revisions(comments)
    uplift = []
    for a in atts:
        if a["is_obsolete"] or not any(f["name"] == flag and f["status"] == "?" for f in a["flags"]):
            continue
        entry = {"attachment": a["id"], "summary": a["summary"], "content_type": a["content_type"]}
        if a["content_type"] == "text/x-phabricator-request":
            m = REVISION_RE.search(base64.b64decode(a["data"]).decode(errors="replace"))
            entry["revision"] = m.group(1) if m else None
            entry["original"] = originals.get(a["id"])
        uplift.append(entry)

    # Main landings the branch doesn't have as ancestors (anything older already is on it), and
    # the branch's own uplifts, which carry new SHAs.
    main_ref, branch_ref = tree.ref("main"), tree.ref(branch)
    main = tree.landings(bug, f"{branch_ref}..{main_ref}")
    on_branch = tree.landings(bug, f"{main_ref}..{branch_ref}")
    branch_subjects = {strip_trailer(c["subject"]) for c in on_branch if not c["revert"]}
    live = [c for c in main if not c["revert"] and not c["backed_out"]]
    for c in live:
        c["on_branch"] = strip_trailer(c["subject"]) in branch_subjects
    pending = [c for c in reversed(live) if not c["on_branch"]]  # oldest first
    for c in pending:
        c["numstat"] = tree.numstat(c["sha"])
    files = sorted({r["file"] for c in pending for r in c["numstat"]})

    churn_before = churn_after = None
    if pending:
        own = {c["sha"] for c in main}
        before = [c for c in tree.log(f"{tree.ref(branch)}..{pending[0]['sha']}^", "--", *files)
                  if c["sha"] not in own]
        touched = set().union(*(tree.touching_fix_lines(c["sha"], branch_ref) for c in pending))
        missing = [c for c in before if not set(c["bugs"]) & uplifted]
        for c in missing:
            c["touches_fix_lines"] = c["sha"] in touched
        churn_before = {
            "already_uplifted": sum(1 for c in before if set(c["bugs"]) & uplifted),
            "commits": missing,
        }
        churn_after = [c for c in tree.log(f"{pending[-1]['sha']}..{tree.ref('main')}", "--", *files)
                       if c["sha"] not in own]

    after_cut = tree.bugs_in([str(b) for b in info["depends_on"]], f"{branch_ref}..{main_ref}")

    def status(b):
        x = linked_info.get(str(b))
        return {"id": b, "status": x["status"], "resolution": x["resolution"]} if x else {"id": b, "status": None}

    return {
        "bug": bug,
        "summary": info["summary"],
        "severity": info["severity"],
        "keywords": info["keywords"],
        "status_flags": {k: v for k, v in info.items() if k.startswith("cf_status_firefox") and v != "---"},
        "regressed_by": info["regressed_by"],
        "regressions": [status(b) for b in info["regressions"]],
        # Missing: landed on main after the branch was cut, and never uplifted.
        "depends_on": [{**status(b), "missing_from_branch": str(b) in after_cut and str(b) not in uplifted}
                       for b in info["depends_on"]],
        "uplift": {"attachments": uplift, "form": latest_form(comments, channel)},
        "landings": {"main": main, "branch": on_branch,
                     # On a newer branch: main landings it has as ancestors (they landed before it
                     # was cut), plus its own uplifts.
                     "newer": {n: [c for c in live if tree.is_ancestor(c["sha"], tree.ref(n))]
                               + tree.landings(bug, f"{main_ref}..{tree.ref(n)}") for n in newer}},
        "pending": pending,
        "apply": tree.apply([c["sha"] for c in pending], branch) if pending else None,
        "churn_before": churn_before,
        "churn_after": churn_after,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("channel", help="beta, release or esrNNN")
    ap.add_argument("bugs", nargs="*", help="default: the dashboard's <channel>.uplift-requested")
    ap.add_argument("--repo", help="Firefox checkout (default: the relnotes scripts' one)")
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()

    keys = [k for k, _ in channels()]
    if args.channel not in keys:
        sys.exit(f"error: no channel {args.channel}; the dashboard has {', '.join(keys)}")
    # Branches closer to Nightly: an ESR request's patch may already be on release and beta.
    newer = [k for k in keys[:keys.index(args.channel)] if not k.startswith("esr")]

    tree = Tree(trainlib.resolve_repo(args.repo), trainlib.gecko_remote())
    if not args.no_fetch:
        trainlib.fetch_origin(tree.repo, "Landings and churn may be out of date.", remote=tree.remote)
    uplifted = tree.uplifted_bugs(args.channel)

    out = {"channel": args.channel, "repo": str(tree.repo),
           "branch_head": tree.git("log", "-1", "--format=%h %cs", tree.ref(args.channel)).strip(),
           "bugs": [], "errors": []}
    for bug in args.bugs or dashboard_bugs(args.channel):
        try:
            out["bugs"].append(facts(bug, args.channel, args.channel, newer, tree, uplifted))
        except (RuntimeError, KeyError, IndexError) as e:
            out["errors"].append({"bug": bug, "error": f"{type(e).__name__}: {e}"})
    json.dump(out, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
