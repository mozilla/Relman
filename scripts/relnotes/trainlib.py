"""Shared helpers for the release-note scripts: train state, build↔commit mapping, scan watermark.

Imported by the sibling scripts via a sys.path insert, so it works regardless of the working
directory the scripts are run from.

**Why build IDs matter here.** Commit dates on `firefox-main` are non-monotonic: a commit merged
from autoland keeps its original committer date, so `git rev-list --before=<date>` can land
arbitrarily far along the ancestry. Measured case: picking a start by "24 hours ago" chose a commit
dated 20:06 that sat *41 commits later in ancestry* than a build boundary dated 18:32, and covered
only 29 of that build's 57 bugs. **Never choose a scan boundary by date.** Use a nightly build's
commit, a cycle tag, or a stored watermark.

The build→commit chain uses two public hg endpoints:

  json-firefoxreleases            build id -> hg node
  json-rev/<node>  ["git_commit"] hg node  -> git commit

Verified end to end: build 20260731085738 resolves to a git range whose bug set matches
whattrainisitnow.com's list for that build exactly (57/57, nothing either way).
"""

import json
import os
import re
import subprocess
import sys
import textwrap
import time
import urllib.error as url_error
import urllib.request as url_request
from pathlib import Path

PRODUCT_DETAILS = "https://product-details.mozilla.org/1.0/firefox_versions.json"
HG_BASE = "https://hg-edge.mozilla.org/mozilla-central"
FIREFOX_RELEASES = f"{HG_BASE}/json-firefoxreleases"
USER_AGENT = "Relman-relnotes/1.0"

# Per-user, outside the repo: this is used by several people on the team and a scan
# should not dirty a shared working tree.
STATE_DIR = Path(
    os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
) / "relman-relnotes"
WATERMARK_FILE = STATE_DIR / "watermark.json"
# Machine-specific settings (currently just the Gecko checkout path). Kept out of the
# repo for the same reason as the watermark: the clone lives somewhere different on every
# machine, so a path committed to the shared tree is wrong for everyone but its author.
CONFIG_FILE = STATE_DIR / "config.json"
# Only a fallback for someone who has never run check-setup. Do not hardcode this anywhere else.
LEGACY_REPO_DEFAULT = Path.home() / "repos" / "firefox"
GECKO_MARKER = Path("modules") / "libpref" / "init" / "StaticPrefList.yaml"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "relman-relnotes"
# json-firefoxreleases is ~12 MB; don't refetch it on every invocation.
BUILDS_CACHE = CACHE_DIR / "firefoxreleases.json"
BUILDS_CACHE_TTL = 3 * 3600


def preview(text: str, limit: int = 1200) -> str:
    """A comment flattened to one line, marked if anything was cut.

    Shared so that every place a Bugzilla comment is summarised has the same guarantee: a cut is
    always visible. A silent 400-character cap in one caller took the deciding line out of bug
    2053681's comment 0 and a note shipped scoped to one platform; the same silent cap in another
    caller would have hidden 2,000 characters of a developer's objection. The limit varies by
    display context, the marker does not.
    """
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return (f"{flat[:limit]} [+{len(flat) - limit} chars cut -- "
            "bug-detail.py <bug> --comment N for the whole comment]")


def fetch_text(url: str, timeout: int = 120, attempts: int = 3, errors: str = "strict") -> str:
    """GET a URL as text, retrying transient failures.

    Nucleus in particular returns a 502 or times out fairly often and then serves the same
    request fine seconds later, so a single failed attempt is not evidence the endpoint is
    down. Only 5xx, 429 and connection errors are retried; a 4xx is a real answer.

    Decoding is strict by default so that a body which does not match its declared charset fails
    loudly. `errors="replace"` suits prose, where a mangled character is better than no page; it
    does not suit data, where a substituted character silently becomes a bug summary that reads as
    fact.
    """
    last = None
    for attempt in range(1, attempts + 1):
        req = url_request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with url_request.urlopen(req, timeout=timeout) as r:
                charset = r.headers.get_content_charset() or "utf-8"
                return r.read().decode(charset, errors=errors)
        except url_error.HTTPError as e:
            last = RuntimeError(f"{url} returned HTTP {e.code} {e.reason}")
            if e.code < 500 and e.code != 429:
                raise last from e
        except (url_error.URLError, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            last = RuntimeError(f"could not reach {url}: {reason}")
        if attempt < attempts:
            delay = 2 ** attempt
            print(f"# {last}; retrying in {delay}s ({attempt}/{attempts - 1})", file=sys.stderr)
            time.sleep(delay)
    raise last


def fetch_json(url: str, timeout: int = 120, attempts: int = 3):
    """GET and parse JSON. Retry behaviour is fetch_text's."""
    return json.loads(fetch_text(url, timeout, attempts))


def write_json_atomic(path: Path, data, pretty: bool = True) -> Path:
    """Write via a sibling temp file and rename, so an interrupted run cannot truncate the target.

    `pretty` suits the small state and config files a human may open. Pass False for bulk caches:
    indenting and re-sorting a multi-megabyte upstream payload costs size for no benefit, and
    reordering its keys means a diff between two cached copies no longer shows what changed upstream.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n" if pretty
                   else json.dumps(data))
    tmp.replace(path)
    return path


def read_config() -> dict:
    """Machine-specific settings, or {} if there are none.

    A *malformed* config is reported rather than swallowed: silently returning {} would send
    `resolve_repo` on to the legacy default, so every subsequent answer would describe a different
    checkout than the one configured, with nothing on screen to say so.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except ValueError as e:
        print(f"# WARNING: {CONFIG_FILE} is not valid JSON ({e}); ignoring it. "
              "Re-run watchlist.py check-setup --repo <path> to rewrite it.", file=sys.stderr)
        return {}
    except OSError as e:
        print(f"# WARNING: cannot read {CONFIG_FILE} ({e}); ignoring it.", file=sys.stderr)
        return {}


def write_config(**kv) -> Path:
    cfg = read_config()
    cfg.update({k: v for k, v in kv.items() if v is not None})
    return write_json_atomic(CONFIG_FILE, cfg)


def is_gecko_checkout(path: Path) -> bool:
    """A git checkout that actually contains Gecko, not just any repo."""
    return (path / ".git").exists() and (path / GECKO_MARKER).exists()


def resolve_repo(cli: str | None = None) -> Path:
    return resolve_repo_with_source(cli)[0]


def resolve_repo_with_source(cli: str | None = None) -> tuple[Path, str]:
    """Resolve the Gecko checkout and say which source won.

    Callers that report provenance need the source, not just the path: labelling an
    environment-variable override as "saved state" misreports the one thing a setup check exists to
    tell you.

    Order: --repo, then $RELMAN_GECKO_REPO, then saved state.

    Exits with instructions rather than guessing. `~/repos/firefox` is tried last and only
    as a legacy convenience; every other consumer should go through this function so there
    is exactly one place that knows where the clone is.
    """
    # An explicit request fails loudly rather than falling back: silently substituting a
    # different tree would report results for a window nobody asked about.
    for source, raw in (("--repo", cli),
                        ("$RELMAN_GECKO_REPO", os.environ.get("RELMAN_GECKO_REPO"))):
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not is_gecko_checkout(path):
            why = ("a git checkout, but has no Gecko source in it"
                   if (path / ".git").exists() else "not a git checkout")
            sys.exit(f"error: {source}={path} is {why} "
                     f"(expected to find {GECKO_MARKER})")
        return path, source

    candidates = []
    saved = read_config().get("gecko_repo")
    if saved:
        candidates.append((f"saved state ({CONFIG_FILE})", saved))
    candidates.append(("legacy default", str(LEGACY_REPO_DEFAULT)))

    tried = []
    for source, raw in candidates:
        path = Path(raw).expanduser()
        if is_gecko_checkout(path):
            return path, source
        tried.append(f"  {source}: {path}"
                     + ("  (git checkout, but no Gecko source in it)"
                        if (path / ".git").exists() else "  (not a git checkout)"))

    sys.exit(
        "error: could not locate the Gecko checkout. Tried:\n"
        + "\n".join(tried)
        + "\n\nSet it once with:\n"
          "  python3 scripts/relnotes/watchlist.py check-setup --repo /path/to/firefox\n"
          "which saves it to " + str(CONFIG_FILE) + " and reports the Claude Code\n"
          "permission entries needed so gecko reads stop prompting."
    )


def fetch_origin(repo: Path, consequence: str, timeout: int | None = None) -> bool:
    """Update the mirror, saying so if it failed. True when the mirror is current.

    A failed fetch used to be discarded, which left every downstream answer describing a stale tree
    while looking exactly like a good run: `pref-delta` resolves defaults from `origin/main` by
    default and prints an `effective now:` verdict the skills quote as *verified*. `consequence` is
    what a stale mirror means for the caller, because "fetch failed" alone does not tell the reader
    whether to trust the numbers underneath it.

    `timeout` is for callers on the critical path of something else, where the fetch is a courtesy
    rather than the point -- offline or off-VPN, an unreachable remote would otherwise hang them.
    It defaults off because a legitimate Gecko fetch can take minutes and cutting one short would
    turn a slow answer into a wrong one.
    """
    print("# fetching origin...", file=sys.stderr)
    try:
        r = subprocess.run(["git", "-C", str(repo), "fetch", "--quiet", "origin"],
                           capture_output=True, text=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"# WARNING: git fetch did not finish within {timeout}s, so the mirror may be behind "
              f"origin. {consequence}", file=sys.stderr)
        return False
    if r.returncode == 0:
        return True
    sys.stderr.write(r.stderr)
    print(f"# WARNING: git fetch exited {r.returncode}, so the mirror may be behind origin. "
          f"{consequence}", file=sys.stderr)
    return False


def git_rc(repo: Path, *args: str) -> tuple[int, str, str]:
    """(exit status, stdout, stderr), for callers where "no output" and "it failed" differ.

    `git` collapses both to "", which suits a value with a sensible default and is wrong for
    anything a safety check reads: an empty file list from a failed `diff` is indistinguishable
    from nothing having changed, and reads as the reassuring answer.
    """
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return r.returncode, r.stdout, r.stderr


def git(repo: Path, *args: str, check: bool = True) -> str:
    rc, out, err = git_rc(repo, *args)
    if rc != 0:
        if check:
            raise RuntimeError(f"git {' '.join(args)} failed: {err.strip()}")
        return ""
    return out


# ------------------------------------------------------------------- tooling version

# This repo's own release-note surface, split by *what it costs to pick up a change* -- which is
# not the same for every file, and that difference is the whole reason the check earns its keep.
#
# Everything here reloads on its own except a skill body. Scripts are subprocessed and reference
# docs are read from disk per call. Permissions look like they would need a restart and do not:
# per the Claude Code docs (checked 2026-08-11), "Claude Code watches your settings files and
# reloads them when they change, so edits to most keys apply to the running session without a
# restart. This includes permissions, hooks, and credential helpers".
#
# Unrelated skills are deliberately excluded -- a commit to triage-user-bugs should not interrupt
# a release-note pass.
#
# Reference docs are live in the sense that matters -- re-reading one picks up the change, no
# /clear -- but they are *not* free, so they are named apart. A copy already read into a
# conversation is exactly as stale as a skill body, and the skill mandates reading the style guide
# before drafting any wording, so a pull that touches these is reported by name rather than waved
# through with the scripts.
TOOLING_DOCS = ("reference/release-notes",)
TOOLING_LIVE = (".claude/settings.json", "scripts/relnotes") + TOOLING_DOCS
# The one exception, and the reason the loud banner exists. A skill body is loaded when the skill
# is invoked and then lives in the conversation, so a pull cannot replace the copy this session
# already read -- and re-invoking would stack a second, contradicting copy beside the first. What
# that produces is nasty precisely because it is quiet: new scripts driven by old instructions,
# mid-pass, with nothing on screen saying so.
#
# `/clear` is the fix and the whole fix: it starts a new conversation with empty context, and the
# system prompt carrying the skill descriptions is "rebuilt on /clear or restart" (same docs). So
# the next invocation reads the new file and only the new file. Quitting the process also works
# and is not necessary -- say `/clear`, because an instruction that overcharges gets ignored.
TOOLING_RECLEAR = (".claude/skills/find-release-note-candidates",
                   ".claude/skills/review-release-notes")
TOOLING_PATHS = TOOLING_RECLEAR + TOOLING_LIVE


RELMAN_ROOT = Path(__file__).resolve().parents[2]


def relman_root() -> Path | None:
    """The Relman checkout these scripts live in, or None if they were copied out of one.

    Not configurable, and deliberately not resolve_repo's business: this is the repo the running
    code came from, so it is always knowable from __file__ without asking anyone. The Gecko clone
    is the opposite case -- somewhere different on every machine, and it has to be told.

    Callers that only need somewhere to write (settings.local.json) want RELMAN_ROOT; callers
    asking git a question want this, because outside a checkout the honest answer is "unknown"
    rather than a path whose every git query fails.
    """
    return RELMAN_ROOT if (RELMAN_ROOT / ".git").exists() else None


def _under(path: str, roots: tuple[str, ...]) -> bool:
    """Is `path` one of `roots` or inside one? Prefix matching alone would put
    `.claude/settings.json.bak` under `.claude/settings.json`."""
    return any(path == r or path.startswith(r + "/") for r in roots)


def tooling_stamp() -> dict:
    """{"commit", "dirty", "version"} -- what is running here, with no upstream question asked.

    Two git calls, no network, no comparison. This is what labelling an artifact needs, and it is
    separated from tooling_status because that one spends five more subprocesses working out
    whether the revision is *behind*, which a stamp does not care about and scan-window pays for
    on every run.
    """
    root = relman_root()
    if root is None:
        return {"commit": "unknown", "dirty": [], "dirty_known": True, "version": "unknown"}
    commit = git(root, "rev-parse", "--short", "HEAD", check=False).strip() or "unknown"
    rc, out, _ = git_rc(root, "status", "--porcelain", "--", *TOOLING_PATHS)
    # Porcelain v1 is `XY <path>`, so the path starts at column 3. Untracked files are excluded to
    # match pull_blocker's reading: `-dirty` means the committed tooling was edited, and a stray
    # scratch file or editor backup under scripts/ drains the marker of exactly that meaning.
    dirty = sorted(ln[3:] for ln in out.splitlines() if ln[3:] and not ln.startswith("??"))
    # The marker rides on the version rather than being reported beside it. A local edit is the
    # single most likely explanation for a transcript that does not match the committed skill, and
    # it only explains anything if it travels with the revision it modifies. `-dirty?` is the third
    # state: the check itself failed, so "clean" was never established.
    suffix = "-dirty" if dirty else ("" if rc == 0 else "-dirty?")
    return {"commit": commit, "dirty": dirty, "dirty_known": rc == 0,
            "version": commit + suffix}


def tooling_status(fetch: bool = True, upstream: str = "origin/main") -> dict:
    """Which revision of the release-note tooling is running, and whether it is behind origin.

    `fetch=False` skips the network but still compares against the mirror, which is what an offline
    or already-fetched caller wants. For the stamp alone -- no comparison at all -- use
    tooling_stamp().
    """
    root = relman_root()
    if root is None:
        return {"available": False,
                "reason": f"{RELMAN_ROOT} is not a git checkout, so the running tooling cannot "
                          "be identified"}

    st: dict = {"available": True, "repo": str(root), "upstream": upstream, "fetched": None}
    if fetch:
        st["fetched"] = fetch_origin(
            root, "The 'behind' count below may miss anything pushed since the last successful "
                  "fetch, so an up-to-date verdict here is not evidence of one.", timeout=30)

    st.update(tooling_stamp())

    st["upstream_known"] = bool(
        git(root, "rev-parse", "--verify", "--quiet", upstream, check=False).strip())
    if not st["upstream_known"]:
        return st

    def count(rev_range: str, scoped: bool) -> int | None:
        out = git(root, "rev-list", "--count", rev_range,
                  *(("--", *TOOLING_PATHS) if scoped else ()), check=False).strip()
        return int(out) if out.isdigit() else None

    # `ahead` keeps someone developing the tooling on a branch from being told to pull their own
    # unmerged work; `behind_any` separates "nothing for you" from "nothing at all", so a quiet
    # verdict is not mistaken for a check that did not run.
    st["behind"] = count(f"HEAD..{upstream}", True)
    st["behind_any"] = count(f"HEAD..{upstream}", False)
    st["ahead"] = count(f"{upstream}..HEAD", True)
    # Three dots: what upstream has added since the merge base. Two would fold in this branch's own
    # commits, listing work you already have as changes waiting to be pulled.
    rc, out, _ = git_rc(root, "diff", "--name-only", f"HEAD...{upstream}", "--", *TOOLING_PATHS)
    st["changed"] = sorted(out.splitlines())
    # This one drives the banner, so its failure mode is the feature's failure mode. An empty list
    # from a `diff` that errored (unrelated histories after a re-pointed remote, say) is
    # indistinguishable from nothing having changed, and classifies a stale skill as harmless.
    # Recorded rather than guessed at: an unclassifiable change is reported in words and the pass
    # continues, since the older tooling still works and refusing to start would cost more than the
    # rare miss. Note `behind > 0 and not changed` is NOT evidence of failure on its own -- an
    # upstream revert pair produces it legitimately.
    st["changed_known"] = rc == 0
    st["needs_clear"] = [f for f in st["changed"] if _under(f, TOOLING_RECLEAR)]
    st["changed_docs"] = [f for f in st["changed"] if _under(f, TOOLING_DOCS)]
    return st


def tooling_summary(st: dict, pull_attempted: bool = False) -> list[str]:
    """`tooling_status` as display lines, first line headline. Shared so that the pass briefing and
    the standalone check cannot drift into two verdicts for the same repo state.

    `pull_attempted` means a pull already ran and printed its own outcome, so this must neither
    repeat that reason nor suggest the command that just produced it.
    """
    if not st.get("available"):
        return [f"unknown -- {st['reason']}"]

    head = st["version"]
    if st.get("ahead"):
        head += f" (+{st['ahead']} local)"
    if not st.get("upstream_known"):
        return [f"{head}; no {st['upstream']} ref here, so there is nothing to compare against"]

    behind = st.get("behind")
    if behind is None:
        lines = [f"{head}; could not count commits against {st['upstream']}"]
    elif behind == 0:
        unrelated = st.get("behind_any") or 0
        lines = [f"{head}, current with {st['upstream']}"
                 + (f" ({unrelated} commit{'' if unrelated == 1 else 's'} behind, none touching "
                    "the tooling)" if unrelated else "")]
        if st.get("fetched") is False:
            lines.append("the fetch failed, so 'current' means only current with what was already "
                         "mirrored")
    else:
        lines = [f"{head}, {behind} commit{'' if behind == 1 else 's'} behind {st['upstream']} "
                 "touching the tooling"]
        # The skill case gets the banner instead of a line here -- a one-liner among six others is
        # exactly how "you are running the wrong instructions" gets skimmed past.
        if not st.get("changed_known", True):
            lines.append("could NOT list which files changed (`git diff` failed), so whether the "
                         "skills are among them is unknown. The older tooling still runs -- carry "
                         "on, but if this pass reads oddly, /clear and start it again")
        elif not st["needs_clear"]:
            if pull_attempted:
                # Reaching here after a pull means it did not happen -- a successful one leaves
                # nothing behind to report. Not fatal: the older scripts still run. Saying so is
                # the whole job, since the alternative is recommending the command that just failed.
                advice = ("the pull above did not happen, so this pass runs on the older copy. "
                          "That works -- but say so when reporting results")
            else:
                blocker = pull_blocker(st)
                advice = (f"but {blocker}" if blocker
                          else "run `check-updates --pull` and carry on in this session")
            lines.append("scripts, docs and settings only, all of which reload on their own -- "
                         + advice)

    if st["dirty"]:
        more = len(st["dirty"]) - 3
        lines.append(f"uncommitted here: {', '.join(st['dirty'][:3])}"
                     + (f" and {more} more" if more > 0 else ""))
    elif not st.get("dirty_known", True):
        lines.append("could not check for local edits (`git status` failed), so the absence of a "
                     "-dirty marker above proves nothing")
    return lines


def pull_blocker(st: dict) -> str:
    """Why a fast-forward pull would be unsafe here, or "" when it is fine to run.

    Three conditions, and `--ff-only` alongside them is what makes pulling on someone's behalf
    reasonable at all: it cannot invent a merge commit and cannot leave a conflicted tree behind
    mid-pass. The other two stop it moving work that is not this tooling. Everything else is a
    human's call, because the cost of guessing wrong is a broken checkout in the middle of a pass.
    """
    root = Path(st["repo"])
    # Derived from the upstream this status was built against, not hardcoded: a blocker judged
    # against origin/main while the counts came from somewhere else is worse than no check.
    upstream = st.get("upstream", "origin/main")
    want = upstream.split("/", 1)[1] if "/" in upstream else upstream
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    if branch != want:
        where = f"on {branch}" if branch and branch != "HEAD" else "on a detached HEAD"
        return (f"this checkout is {where}, not {want}. Pulling would move a branch nobody asked "
                f"to move -- switch to {want}, or pull by hand if that branch is deliberate")
    modified = [ln for ln in git(root, "status", "--porcelain", check=False).splitlines()
                if not ln.startswith("??")]
    if modified:
        return (f"{len(modified)} tracked file(s) have uncommitted changes. Commit or stash them "
                "first; pulling over work in progress is not a trade to make on someone's behalf")
    ahead = git(root, "rev-list", "--count", f"{upstream}..HEAD", check=False).strip()
    if ahead and ahead != "0":
        return (f"{want} here has {ahead} commit(s) origin does not, so this cannot fast-forward. "
                "Push or rebase them first")
    return ""


def pull_tooling(st: dict) -> dict:
    """Fast-forward this checkout to origin/main. Returns {"pulled": bool, "message": str}.

    Refuses rather than improvises: see pull_blocker for the conditions. A refusal is not a
    failure of the pass, it is a fact about the checkout, so it reports and lets the caller decide.
    """
    blocker = pull_blocker(st)
    if blocker:
        return {"pulled": False, "message": f"not pulling: {blocker}."}
    root = Path(st["repo"])
    try:
        r = subprocess.run(["git", "-C", str(root), "pull", "--ff-only"],
                           capture_output=True, text=True, check=False, timeout=120)
    except subprocess.TimeoutExpired:
        return {"pulled": False,
                "message": "not pulled: git pull --ff-only did not finish within 120s; the "
                           "checkout is unchanged."}
    if r.returncode != 0:
        return {"pulled": False,
                "message": f"not pulled: git pull --ff-only exited {r.returncode} and the "
                           f"checkout is unchanged:\n{(r.stderr or r.stdout).strip()}"}
    after = git(root, "rev-parse", "--short", "HEAD", check=False).strip()
    return {"pulled": True, "message": f"pulled: {st.get('commit')} -> {after}"}


def tooling_banner(st: dict, stale: list[str] | None = None, pulled: bool = False,
                   pull_attempted: bool = False) -> list[str]:
    """The unmissable block, or [] when nothing needs it.

    Separate from tooling_summary so it prints flush left under either caller's label column, and
    so the quiet cases stay quiet: a banner that fires when a script changed is a banner people
    learn to scroll past, and then it is not there for the one case that matters.

    Every remaining step is stated at once, because /clear destroys the context that would
    otherwise deliver the step after it. `stale` is passed explicitly once a pull has happened:
    the status then reads "current", but what needs clearing is a fact about the transition, not
    about the state left behind it.
    """
    stale = st.get("needs_clear") if stale is None else stale
    if not stale:
        return []

    # Phrased for the user to say, not for a session to run: /clear destroys the context these
    # instructions live in, so whatever comes after it has to survive as a sentence a person
    # repeats. It names the work rather than being a bare "carry on" because the fresh session has
    # to route it, and this repo holds more than one skill.
    resume_step = 'then say:  "let\'s keep looking for release notes"'
    if pulled:
        steps = ["The new files are on disk. This session is still running the old ones.",
                 "",
                 "    1.  /clear                       <-- REQUIRED, now",
                 f"    2.  {resume_step}"]
    else:
        if pull_attempted:
            # A pull ran and did not land; it printed why. Pointing at that beats restating it in
            # different words ten lines later, and beats suggesting the command that produced it.
            first = "    1.  the pull did not happen -- see the message above"
        else:
            blocker = pull_blocker(st)
            first = (f"    1.  CANNOT PULL AUTOMATICALLY -- {blocker}" if blocker
                     else "    1.  watchlist.py check-updates --pull")
        steps = ["The skill instructions driving it are out of date.",
                 "",
                 first,
                 "    2.  /clear                       <-- REQUIRED",
                 f"    3.  {resume_step}"]

    body = (["STOP -- do not start a pass in this session."] + steps
            + ["",
               "Pulling on its own is NOT enough. A skill body is loaded into",
               "the conversation when the skill is invoked and stays there, so",
               "a new file cannot replace the copy this session already read.",
               "",
               "/clear IS enough. You do not need to quit Claude Code."])
    return _box(body) + [f"out of date here: {', '.join(stale)}"]


def _box(lines: list[str], width: int = 62) -> list[str]:
    """Frame `lines`, wrapping any that run long and sizing the frame to what results.

    Both halves are load-bearing. A hardcoded frame width breaks the first time someone edits a
    line past it; sizing to the content instead lets one interpolated sentence -- a pull blocker
    naming a branch -- stretch the frame to 190 columns. A box that has come apart reads as broken
    output, which is the opposite of what a message this size is for.
    """
    wrapped: list[str] = []
    for ln in lines:
        if len(ln) <= width:
            wrapped.append(ln)
            continue
        # A wrapped step hangs under its text rather than its number, so it still reads as one
        # step; flush-left prose just keeps wrapping flush left.
        lead = len(ln) - len(ln.lstrip())
        indent = " " * (lead + 4) if lead else ""
        wrapped.extend(textwrap.wrap(ln, width=width, subsequent_indent=indent))
    w = max(len(ln) for ln in wrapped)
    rule = "*" * (w + 10)
    return [rule] + [f"***  {ln.ljust(w)}  ***" for ln in wrapped] + [rule]


# --------------------------------------------------------------------------- trains


def train_versions() -> dict:
    d = fetch_json(PRODUCT_DETAILS)

    def major(key):
        m = re.match(r"(\d+)", d.get(key) or "")
        return int(m.group(1)) if m else None

    return {
        "nightly": major("FIREFOX_NIGHTLY"),
        "beta": major("LATEST_FIREFOX_DEVEL_VERSION"),
        "release": major("LATEST_FIREFOX_VERSION"),
        "esr": [v for v in (major("FIREFOX_ESR"), major("FIREFOX_ESR_NEXT")) if v],
        "next_merge": d.get("NEXT_MERGE_DATE"),
        "last_merge": d.get("LAST_MERGE_DATE"),
    }


def cycle_range(repo: Path, version: int, head: str = "origin/main") -> tuple | None:
    """The nightly cycle for a version, as (start, end, in_progress).

    There is no FIREFOX_NIGHTLY_{N}_BASE tag -- only _END exists -- so the cycle start
    is the previous version's _END.

    The closing tag does not exist until merge day, and the most useful moment to run a
    cycle pass is *before* that: deciding rollup notes while there is still time to act
    on them. So when the end tag is missing, run to HEAD and report the cycle as still
    in progress rather than refusing.
    """
    start = f"FIREFOX_NIGHTLY_{version - 1}_END"
    end = f"FIREFOX_NIGHTLY_{version}_END"
    if not git(repo, "rev-parse", "--verify", "--quiet", start, check=False).strip():
        return None
    if git(repo, "rev-parse", "--verify", "--quiet", end, check=False).strip():
        return start, end, False
    return start, head, True


# ------------------------------------------------------------------- builds -> commits


def load_builds(refresh: bool = False) -> list[dict]:
    """All Firefox builds from hg, cached on disk (the payload is ~12 MB)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fresh = (
        BUILDS_CACHE.exists()
        and not refresh
        and (time.time() - BUILDS_CACHE.stat().st_mtime) < BUILDS_CACHE_TTL
    )
    if fresh:
        try:
            return json.loads(BUILDS_CACHE.read_text())["builds"]
        except (ValueError, KeyError) as e:
            # Refetching repairs it, so this is not fatal -- but say so, because a write that keeps
            # failing (a full disk) would otherwise refetch 12 MB on every run in silence.
            print(f"# warning: rewriting unreadable builds cache {BUILDS_CACHE} ({e})",
                  file=sys.stderr)
    payload = fetch_json(FIREFOX_RELEASES)
    # Atomic, and unindented: this is the bulk cache write_json_atomic's `pretty=False` exists for.
    write_json_atomic(BUILDS_CACHE, payload, pretty=False)
    return payload["builds"]


def nightly_builds(limit: int = 12, refresh: bool = False) -> list[dict]:
    """Most recent nightly builds, newest first, deduped by build id.

    The same build id appears once per platform with the same changeset, so dedupe.
    """
    seen: dict[str, dict] = {}
    for b in load_builds(refresh=refresh):
        if b.get("channel") != "nightly":
            continue
        bid = b.get("buildid")
        if bid and bid not in seen:
            seen[bid] = {"buildid": bid, "node": b.get("node"),
                         "app_version": b.get("app_version")}
    return [seen[k] for k in sorted(seen, reverse=True)[:limit]]


def builds_on_day(date: str, refresh: bool = False) -> list[dict]:
    """Every nightly build whose id starts with YYYYMMDD, oldest first."""
    return sorted(
        (b for b in nightly_builds(limit=100000, refresh=refresh)
         if b["buildid"].startswith(date)),
        key=lambda b: b["buildid"],
    )


def day_boundaries(date: str, refresh: bool = False) -> tuple[str, str] | None:
    """(previous build id, last build id of the day) -- the day's full landing span.

    Nightly ships 2-3 times a day at irregular times, and Release Management reviews a
    whole day at once. A day's landings run from the *previous day's last build* up to
    this day's last build, since the first build of the day covers everything since the
    one before it.
    """
    all_ids = sorted(b["buildid"] for b in nightly_builds(limit=100000, refresh=refresh))
    day = [i for i in all_ids if i.startswith(date)]
    if not day:
        return None
    first_idx = all_ids.index(day[0])
    if first_idx == 0:
        return None
    return all_ids[first_idx - 1], day[-1]


def hg_to_git(node: str) -> str | None:
    """hg changeset -> git commit, via the `git_commit` field on json-rev."""
    try:
        rev = fetch_json(f"{HG_BASE}/json-rev/{node}")
    except RuntimeError as e:
        # Name the reason: callers turn a None into "could not map the build boundaries to git
        # commits", which describes a mapping problem when the cause was hg being unreachable.
        print(f"# WARNING: could not map hg {node[:12]} to a git commit ({e})", file=sys.stderr)
        return None
    return rev.get("git_commit")


def resolve_build(build_id: str, refresh: bool = False) -> dict | None:
    """{buildid, node, git} for a nightly build id."""
    for b in nightly_builds(limit=100000, refresh=refresh):
        if b["buildid"] == build_id:
            g = hg_to_git(b["node"])
            return {**b, "git": g}
    return None


def annotate_builds(builds: list[dict], repo: Path) -> list[dict]:
    """Add the git commit and its local date to each build, skipping ones not in the mirror."""
    out = []
    for b in builds:
        g = hg_to_git(b["node"]) if b.get("node") else None
        date = ""
        if g:
            date = git(repo, "log", "-1", "--format=%cd", "--date=iso", g, check=False).strip()
        out.append({**b, "git": g, "git_date": date, "in_mirror": bool(date)})
    return out


# ----------------------------------------------------------------------- watermark


def read_watermark() -> dict | None:
    """The stored scan position, or None if there isn't one.

    An unreadable file is reported rather than folded into "there isn't one": those lead to opposite
    actions. Read as absence, `--show-state` offers a fresh start and the scan position is quietly
    lost; reported, you know to restore or re-set it.
    """
    if not WATERMARK_FILE.exists():
        return None
    try:
        return json.loads(WATERMARK_FILE.read_text())
    except ValueError as e:
        print(f"# WARNING: {WATERMARK_FILE} is not readable JSON ({e}); continuing as if there "
              "were no watermark, so --since-last will NOT resume where you left off.",
              file=sys.stderr)
        return None


def write_watermark(commit: str, note: str = "", repo: Path | None = None,
                    allow_regress: bool = False) -> Path:
    """Record the scan position, refusing to move it backwards by default.

    --save-state writes the window end, which silently regresses the watermark if someone
    re-scans an older window -- and the next --since-last then re-reports every day in
    between. Only advance unless explicitly told otherwise.
    """
    if repo is not None and not allow_regress:
        prev = (read_watermark() or {}).get("commit")
        if prev and git(repo, "rev-parse", "--verify", "--quiet", prev, check=False).strip():
            # New position already an ancestor of the stored one => this is a rescan of
            # older history, not progress.
            r = subprocess.run(
                ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, prev],
                capture_output=True, check=False,
            )
            if r.returncode == 0 and commit != prev:
                raise RuntimeError(
                    f"refusing to move the watermark backwards: {commit[:12]} is an ancestor "
                    f"of the stored {prev[:12]}. This looks like a rescan of older history; "
                    "the watermark was left alone so the next --since-last does not re-report "
                    "days already reviewed."
                )
    return write_json_atomic(WATERMARK_FILE, {
        "commit": commit,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": note,
    })


def watermark_status(repo: Path, wm: dict | None, nightly: int) -> dict:
    """Is a stored watermark still a sensible place to resume from?

    A watermark from a previous train is usually the wrong start: the user has likely
    been away a cycle or more, and resuming would sweep in thousands of commits that
    belong to an already-shipped version.
    """
    if not wm or not wm.get("commit"):
        return {"present": False}
    commit = wm["commit"]
    if not git(repo, "rev-parse", "--verify", "--quiet", commit, check=False).strip():
        return {"present": True, "known": False, "commit": commit}
    date = git(repo, "log", "-1", "--format=%cd", "--date=iso", commit, check=False).strip()
    ahead = git(repo, "rev-list", "--count", f"{commit}..origin/main", check=False).strip()
    prev_cycle_end = f"FIREFOX_NIGHTLY_{nightly - 1}_END"
    stale_train = False
    if git(repo, "rev-parse", "--verify", "--quiet", prev_cycle_end, check=False).strip():
        # If the watermark predates the current cycle's start, it's from an older train.
        r = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, prev_cycle_end],
            capture_output=True, check=False,
        )
        stale_train = r.returncode == 0
    return {
        "present": True,
        "known": True,
        "commit": commit,
        "date": date,
        "commits_behind": int(ahead) if ahead.isdigit() else None,
        "stale_train": stale_train,
        "current_cycle_start": prev_cycle_end,
        "saved_at": wm.get("saved_at"),
        "note": wm.get("note"),
    }
