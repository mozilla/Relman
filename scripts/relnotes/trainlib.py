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


def fetch_json(url: str, timeout: int = 120, attempts: int = 3):
    """GET and parse JSON, retrying transient failures.

    Nucleus in particular returns a 502 or times out fairly often and then serves the same
    request fine seconds later, so a single failed attempt is not evidence the endpoint is
    down. Only 5xx, 429 and connection errors are retried; a 4xx is a real answer.
    """
    last = None
    for attempt in range(1, attempts + 1):
        req = url_request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with url_request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
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


def git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        if check:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return ""
    return r.stdout


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
        except (ValueError, KeyError):
            pass
    payload = fetch_json(FIREFOX_RELEASES)
    BUILDS_CACHE.write_text(json.dumps(payload))
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
    except RuntimeError:
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
