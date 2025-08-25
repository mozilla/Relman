"""
as-merge-day.py — Automate Application Services "merge day" steps from a fork.

OVERVIEW
--------
This script performs two phases in sequence, designed to run from a clone of your
*fork* (origin) with an "upstream" remote pointing at the real upstream repo.

PHASE A: Release branch (release-vN)
  1) Auto-detect the highest upstream branch of the form "release-vN". (Or use --version N)
  2) Create/reset local branch "release-vN" from "upstream/release-vN".
  3) Update files on that branch:
       - version.txt: drop a trailing "a1" so "N.0a1" -> "N.0".
       - CHANGELOG.md (top vN.0 section only):
           "# vN.0 (In progress)" -> "# vN.0 (_YYYY-MM-DD_)"
           first "[Full Changelog](In progress)" -> compare link v(N-1).0...vN.0
  4) Commit with message: "Cut release vN.0".
  5) Push "release-vN" to your fork (origin) and print a PR URL targeting upstream/release-vN.

PHASE B: Main branch (start next cycle)
  6) Create local branch "start-release-v(N+1)" from "upstream/main".
  7) Update files on that branch:
       - version.txt -> "N+1.0a1".
       - CHANGELOG.md:
           * Prepend a new top section:
               "# v(N+1).0 (In progress)\n\n[Full Changelog](In progress)\n\n"
           * "Close out" the previous top section vN.0 in-place:
               "# vN.0 (In progress)" -> "# vN.0 (_YYYY-MM-DD_)"
               first "[Full Changelog](In progress)" -> compare link v(N-1).0...vN.0
  8) Commit with message: "Start release v(N+1).0".
  9) Push "start-release-v(N+1)" to your fork (origin) and print a PR URL targeting upstream/main.

ASSUMPTIONS
-----------
- You run in a local clone of your fork (so "origin" exists and points to your fork).
- "upstream" remote exists; if missing, the script adds it pointing to:
      https://github.com/mozilla/application-services.git
  If you're testing with a sandbox upstream, set it yourself beforehand:
      git remote add upstream https://github.com/<org-or-user>/application-services-<whatever>.git
- CHANGELOG.md has a top section header of the form "# vN.0 (In progress)" and
  contains a single "[Full Changelog](In progress)" placeholder in that section.
- Major versions increment by 1 each release (N -> N+1).
- Python >= 3.9 (for zoneinfo). Fallback uses local system time if zoneinfo is unavailable.

USAGE
-----
# Typical run (auto-detect highest release-vN)
python as-merge-day.py

# Control output verbosity
python as-merge-day.py --quiet
python as-merge-day.py --verbose
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime

# -------------
# Logging utils
# -------------
_VERBOSE = False
_QUIET = False

def log(msg: str) -> None:
    """Standard log (suppressed in --quiet)."""
    if not _QUIET:
        print(msg)

def vlog(msg: str) -> None:
    """Verbose log (printed only in --verbose)."""
    if _VERBOSE and not _QUIET:
        print(msg)


# ----------------------------
# Shell / Git helper functions
# ----------------------------

def run(cmd, cwd=None, need_output=False):
    """
    Run a subprocess command in 'cwd'.

    - If need_output=True, returns stdout (string) and always captures output.
    - If need_output=False, behavior depends on verbosity:
        * --verbose: stream live output (capture=False).
        * default/quiet: capture output to keep console clean (but discard it).
    Raises CalledProcessError on failure.
    """
    if need_output:
        result = subprocess.run(
            cmd, cwd=cwd, check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        return result.stdout.strip()
    else:
        subprocess.run(
            cmd, cwd=cwd, check=True, text=True,
            stdout=None if _VERBOSE else subprocess.PIPE,
            stderr=None if _VERBOSE else subprocess.PIPE
        )
        return ""


def ensure_upstream_remote(repo: str) -> None:
    """
    Ensure a git remote named 'upstream' exists. If missing, add it pointing to the
    official mozilla/application-services repo. If you're testing with a sandbox, set
    your upstream remote yourself *before* running the script.
    """
    remotes = run(["git", "remote"], cwd=repo, need_output=True).splitlines()
    if "upstream" not in remotes:
        url = "https://github.com/mozilla/application-services.git"
        log(f"[info] Adding remote 'upstream' -> {url}")
        run(["git", "remote", "add", "upstream", url], cwd=repo)


def require_origin_remote(repo: str) -> None:
    """
    Ensure 'origin' exists; this should be true if you cloned your fork.
    """
    remotes = run(["git", "remote"], cwd=repo, need_output=True).splitlines()
    if "origin" not in remotes:
        print("ERROR: 'origin' remote not found. Please run this from a clone of your fork.", file=sys.stderr)
        sys.exit(2)


def parse_remote_owner_repo(repo: str, remote_name: str):
    """
    Return (owner, repo_name) parsed from the given remote.
    Supports SSH and HTTPS GitHub remote formats.
    Returns (None, None) if parse fails.
    """
    try:
        url = run(["git", "remote", "get-url", remote_name], cwd=repo, need_output=True)
    except subprocess.CalledProcessError:
        return None, None

    owner = None
    name = None

    if url.startswith("git@github.com:"):          # SSH e.g., git@github.com:owner/repo.git
        path = url.split(":", 1)[1]
        owner, name = path.split("/", 1)
    elif url.startswith("https://github.com/"):     # HTTPS e.g., https://github.com/owner/repo(.git)
        path = url.split("https://github.com/", 1)[1]
        owner, name = path.split("/", 1)

    if name and name.endswith(".git"):
        name = name[:-4]

    return owner, name


def list_upstream_release_versions(repo: str):
    """
    Query upstream for all heads, filter those named 'release-v<digits>',
    and return a sorted list of ints [N1, N2, ...] ascending.
    """
    out = run(["git", "ls-remote", "--heads", "upstream"], cwd=repo, need_output=True)
    versions = []
    for line in out.splitlines():
        # Format: <sha>\trefs/heads/<branch>
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/heads/release-v"):
            name = ref.split("/", 2)[-1]  # e.g., "release-v144"
            m = re.fullmatch(r"release-v(\d+)", name)
            if m:
                versions.append(int(m.group(1)))
    versions.sort()
    return versions


def checkout_release_branch(repo: str, version: int) -> None:
    """
    Ensure local branch 'release-v{version}' is created/reset from 'upstream/release-v{version}'.
    """
    remote_branch = f"upstream/release-v{version}"
    log(f"[info] Fetching {remote_branch}")
    run(["git", "fetch", "upstream", f"release-v{version}"], cwd=repo)
    log(f"[info] Checking out local branch release-v{version} from {remote_branch}")
    run(["git", "checkout", "-B", f"release-v{version}", remote_branch], cwd=repo)


def checkout_main_work_branch(repo: str, next_version: int) -> str:
    """
    Create/reset a local branch for main-line changes off upstream/main.
    We avoid working directly on local 'main' to prevent accidental pushes.
    """
    work_branch = f"start-release-v{next_version}"
    log(f"[info] Fetching upstream/main")
    run(["git", "fetch", "upstream", "main"], cwd=repo)
    log(f"[info] Checking out local branch {work_branch} from upstream/main")
    run(["git", "checkout", "-B", work_branch, "upstream/main"], cwd=repo)
    return work_branch


# ----------------------------
# File read/write convenience
# ----------------------------

def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ----------------------------
# PHASE A: release branch edits
# ----------------------------

def update_version_txt_release(repo: str):
    """
    On the release branch:
      - If version.txt ends with 'a1', drop it: 'N.0a1' -> 'N.0'
      - If it doesn't end with 'a1', we leave it as-is.
    Returns (changed: bool, new_value: str)
    """
    path = os.path.join(repo, "version.txt")
    if not os.path.exists(path):
        print("ERROR: version.txt not found at repo root", file=sys.stderr)
        sys.exit(2)

    original = read_file(path)
    stripped = original.strip()

    # Drop trailing 'a1' if present (ignore trailing whitespace)
    new_value = re.sub(r"a1\s*$", "", stripped)
    changed = (new_value != stripped)

    if changed:
        log(f"[change] version.txt (release): '{stripped}' -> '{new_value}'")
        write_file(path, new_value + "\n")
    else:
        vlog("[info] version.txt (release): no trailing 'a1'; no change")

    return changed, new_value


def update_changelog_release(repo: str, version: int, date_str: str, prev_version: int):
    """
    In CHANGELOG.md’s *top* v{version}.0 section:
      1) "# v{version}.0 (In progress)" -> "# v{version}.0 (_{date_str}_)"
      2) First "[Full Changelog](In progress)" -> compare link v{prev_version}.0...v{version}.0
    Edits are strictly scoped to that section.
    Returns (changed: bool, compare_url: str)
    """
    path = os.path.join(repo, "CHANGELOG.md")
    if not os.path.exists(path):
        print("ERROR: CHANGELOG.md not found at repo root", file=sys.stderr)
        sys.exit(2)

    md = read_file(path)

    header_re = re.compile(rf"^#\s*v{version}\.0\s*\(In progress\)", re.IGNORECASE | re.MULTILINE)
    header_match = header_re.search(md)
    if not header_match:
        vlog(f"[warn] (release) '# v{version}.0 (In progress)' not found")
        compare_url = f"https://github.com/mozilla/application-services/compare/v{prev_version}.0...v{version}.0"
        return False, compare_url

    # Boundaries of the v{version}.0 section
    section_start = header_match.start()
    any_header_re = re.compile(r"^#\s*v\d+\.\d+\s*\(.+?\)", re.MULTILINE)
    next_header_match = any_header_re.search(md, header_match.end())
    section_end = next_header_match.start() if next_header_match else len(md)

    before = md[:section_start]
    section = md[section_start:section_end]
    after = md[section_end:]

    changed = False

    # 1) Header -> dated
    new_header = f"# v{version}.0 (_{date_str}_)"
    section, n_header_subs = re.subn(
        rf"^#\s*v{version}\.0\s*\(In progress\)",
        new_header,
        section,
        count=1,
        flags=re.IGNORECASE | re.MULTILINE
    )
    if n_header_subs:
        log(f"[change] CHANGELOG.md (release): header -> '{new_header}'")
        changed = True

    # 2) Full Changelog placeholder -> compare link
    compare_url = f"https://github.com/mozilla/application-services/compare/v{prev_version}.0...v{version}.0"
    new_fc = f"[Full Changelog]({compare_url})"
    section, n_fc_subs = re.subn(
        r"\[Full Changelog\]\((?:In progress)\)",
        new_fc,
        section,
        count=1,
        flags=re.IGNORECASE
    )
    if n_fc_subs:
        log(f"[change] CHANGELOG.md (release): 'Full Changelog' -> {compare_url}")
        changed = True

    if changed:
        write_file(path, before + section + after)

    return changed, compare_url


# ----------------------------
# PHASE B: main branch edits
# ----------------------------

def update_version_txt_main(repo: str, next_version: int):
    """
    On the main work branch:
      - Write version.txt as '{next_version}.0a1' (exact).
    Returns (changed: bool, new_value: str)
    """
    path = os.path.join(repo, "version.txt")
    if not os.path.exists(path):
        print("ERROR: version.txt not found at repo root", file=sys.stderr)
        sys.exit(2)

    target = f"{next_version}.0a1\n"
    current = read_file(path)
    if current == target:
        vlog("[info] version.txt (main): already correct; no change")
        return False, target.strip()

    log(f"[change] version.txt (main): -> '{target.strip()}'")
    write_file(path, target)
    return True, target.strip()


def update_changelog_main_start_next(repo: str, version: int, date_str: str):
    """
    On the main work branch:
      1) Prepend new top section:
           "# v{version+1}.0 (In progress)\n\n[Full Changelog](In progress)\n\n"
      2) Close out the now-second section (v{version}.0):
           "# v{version}.0 (In progress)" -> "# v{version}.0 (_{date_str}_)"
           first "[Full Changelog](In progress)" -> compare link v{version-1}.0...v{version}.0
    Returns (changed: bool, compare_url: str)
    """
    path = os.path.join(repo, "CHANGELOG.md")
    if not os.path.exists(path):
        print("ERROR: CHANGELOG.md not found at repo root", file=sys.stderr)
        sys.exit(2)

    md = read_file(path)

    # Locate current top header "# v{version}.0 (In progress)".
    header_re = re.compile(rf"^#\s*v{version}\.0\s*\(In progress\)", re.IGNORECASE | re.MULTILINE)
    header_match = header_re.search(md)
    if not header_match:
        vlog(f"[warn] (main) '# v{version}.0 (In progress)' not found; will still prepend next section.")
        prev_version = version - 1
        compare_url = f"https://github.com/mozilla/application-services/compare/v{prev_version}.0...v{version}.0"
        new_top = f"# v{version+1}.0 (In progress)\n\n[Full Changelog](In progress)\n\n"
        write_file(path, new_top + md)
        return True, compare_url

    # Determine section bounds for v{version}.0
    section_start = header_match.start()
    any_header_re = re.compile(r"^#\s*v\d+\.\d+\s*\(.+?\)", re.MULTILINE)
    next_header_match = any_header_re.search(md, header_match.end())
    section_end = next_header_match.start() if next_header_match else len(md)

    before = md[:section_start]           # likely empty when top-of-file is correct
    section = md[section_start:section_end]
    after = md[section_end:]

    changed = False

    # Close out header for v{version}.0
    dated_header = f"# v{version}.0 (_{date_str}_)"
    section, n_header_subs = re.subn(
        rf"^#\s*v{version}\.0\s*\(In progress\)",
        dated_header,
        section,
        count=1,
        flags=re.IGNORECASE | re.MULTILINE
    )
    if n_header_subs:
        log(f"[change] CHANGELOG.md (main): header -> '{dated_header}'")
        changed = True

    # Update 'Full Changelog' placeholder within this section
    prev_version = version - 1
    compare_url = f"https://github.com/mozilla/application-services/compare/v{prev_version}.0...v{version}.0"
    new_fc = f"[Full Changelog]({compare_url})"
    section, n_fc_subs = re.subn(
        r"\[Full Changelog\]\((?:In progress)\)",
        new_fc,
        section,
        count=1,
        flags=re.IGNORECASE
    )
    if n_fc_subs:
        log(f"[change] CHANGELOG.md (main): 'Full Changelog' -> {compare_url}")
        changed = True

    # Prepend new top section for {version+1}.0
    new_top = f"# v{version+1}.0 (In progress)\n\n[Full Changelog](In progress)\n\n"
    new_md = before + new_top + section + after

    # We always write because we always prepend a new section.
    write_file(path, new_md)
    return True, compare_url


# ----------------------------
# Main program flow
# ----------------------------

def main():
    global _VERBOSE, _QUIET

    parser = argparse.ArgumentParser(description="Automate Application Services merge day tasks from a fork.")
    parser.add_argument("--version", type=int,
                        help="Override: release version N to use (e.g., 144). "
                             "If omitted, auto-detect the highest upstream release-vN.")
    parser.add_argument("--verbose", action="store_true",
                        help="Stream detailed git output and step-by-step logs.")
    parser.add_argument("--quiet", action="store_true",
                        help="Print only PR URLs (no step logs).")
    args = parser.parse_args()

    _VERBOSE = bool(args.verbose)
    _QUIET = bool(args.quiet)

    repo = os.path.abspath(".")

    # Ensure we're in a git repo.
    try:
        run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo)
    except subprocess.CalledProcessError:
        print("ERROR: This directory is not a git repository.", file=sys.stderr)
        sys.exit(2)

    # Ensure remotes are present.
    require_origin_remote(repo)
    ensure_upstream_remote(repo)

    # Sync remotes.
    log("[info] Fetching upstream (including tags)")
    run(["git", "fetch", "upstream", "--tags"], cwd=repo)
    vlog("[info] Fetching origin")
    run(["git", "fetch", "origin"], cwd=repo)

    # Decide release version N.
    if args.version is not None:
        version = args.version
    else:
        versions = list_upstream_release_versions(repo)
        if not versions:
            print("ERROR: No upstream release-v* branches found. Did you create one upstream?", file=sys.stderr)
            sys.exit(2)
        version = versions[-1]  # highest
    release_branch = f"release-v{version}"
    log(f"[info] Using release branch: {release_branch}")

    # Validate existence of upstream release branch.
    try:
        run(["git", "rev-parse", f"upstream/{release_branch}"], cwd=repo)
    except subprocess.CalledProcessError:
        print(f"ERROR: upstream/{release_branch} does not exist. Did you create it upstream?", file=sys.stderr)
        sys.exit(2)

    # Phase A: release branch changes.
    checkout_release_branch(repo, version)

    # Build YYYY-MM-DD date string (prefer America/Toronto).
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+
        date_str = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    except Exception:
        date_str = datetime.now().strftime("%Y-%m-%d")

    ver_changed_rel, _ = update_version_txt_release(repo)
    ch_changed_rel, compare_url_rel = update_changelog_release(repo, version, date_str, version - 1)

    # Stage, commit (if needed), and push release branch.
    run(["git", "add", "version.txt", "CHANGELOG.md"], cwd=repo)
    commit_msg_rel = f"Cut release v{version}.0"
    # Only commit if something is staged.
    diff_rel = run(["git", "diff", "--cached", "--name-only"], cwd=repo, need_output=True)
    if diff_rel.strip():
        log(f"[info] (release) Committing: {commit_msg_rel}")
        run(["git", "commit", "-m", commit_msg_rel], cwd=repo)
    else:
        vlog("[info] (release) Nothing staged; skipping commit.")

    log(f"[info] (release) Pushing HEAD to origin:{release_branch}")
    run(["git", "push", "-u", "origin", f"HEAD:{release_branch}"], cwd=repo)

    # PR link for release branch: base repo = upstream, head = origin
    up_owner, up_repo = parse_remote_owner_repo(repo, "upstream")
    or_owner, _ = parse_remote_owner_repo(repo, "origin")
    pr_release = None
    if up_owner and up_repo and or_owner:
        pr_release = f"https://github.com/{up_owner}/{up_repo}/compare/{release_branch}...{or_owner}:{release_branch}?expand=1"

    # Phase B: main branch changes.
    next_version = version + 1
    work_branch = checkout_main_work_branch(repo, next_version)

    ver_changed_main, _ = update_version_txt_main(repo, next_version)
    ch_changed_main, compare_url_main = update_changelog_main_start_next(repo, version, date_str)

    # Stage, commit (if needed), and push main work branch.
    run(["git", "add", "version.txt", "CHANGELOG.md"], cwd=repo)
    commit_msg_main = f"Start release v{next_version}.0"
    diff_main = run(["git", "diff", "--cached", "--name-only"], cwd=repo, need_output=True)
    if diff_main.strip():
        log(f"[info] (main) Committing: {commit_msg_main}")
        run(["git", "commit", "-m", commit_msg_main], cwd=repo)
    else:
        vlog("[info] (main) Nothing staged; skipping commit.")

    log(f"[info] (main) Pushing HEAD to origin:{work_branch}")
    run(["git", "push", "-u", "origin", f"HEAD:{work_branch}"], cwd=repo)

    # PR link for main branch: base = upstream/main, head = origin/work_branch
    pr_main = None
    if up_owner and up_repo and or_owner:
        pr_main = f"https://github.com/{up_owner}/{up_repo}/compare/main...{or_owner}:{work_branch}?expand=1"

    # Final summary
    if _QUIET:
        if pr_release: print(pr_release)
        if pr_main: print(pr_main)
    else:
        check = "✔"
        print("\n=== Summary ===")
        print(f"{check} Release branch: {release_branch}")
        print(f"   Commit: {commit_msg_rel}")
        print(f"   PR: {pr_release or 'PR URL unavailable'}")
        print(f"{check} Main branch:    {work_branch}")
        print(f"   Commit: {commit_msg_main}")
        print(f"   PR: {pr_main or 'PR URL unavailable'}")
        print("\nDone.")


if __name__ == "__main__":
    main()
