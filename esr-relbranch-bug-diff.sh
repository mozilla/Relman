#!/usr/bin/env bash
#
# Report bugs landed on an ESR branch since the last release tag that are
# NOT present on the corresponding RELBRANCH.
#
# The release tag and RELBRANCH are auto-detected from the branch:
#   branch   = origin/esr<major>
#   tag      = latest FIREFOX_<major>_*esr_RELEASE ancestor of the branch
#   relbranch = FIREFOX_ESR_<major>_<minor>_X_RELBRANCH (derived from tag)
#
# Usage:
#   esr-relbranch-bug-diff.sh                 # both esr140 and esr115
#   esr-relbranch-bug-diff.sh 140             # just esr140
#   esr-relbranch-bug-diff.sh 115 140         # explicit list
#
# Env:
#   FIREFOX_REPO   path to the firefox clone (default: ~/repos/firefox)

set -euo pipefail

REPO="${FIREFOX_REPO:-$HOME/repos/firefox}"
BUGZILLA_BASE="https://bugzilla.mozilla.org/buglist.cgi"
DEFAULT_VERSIONS=(140 115)

resolve_ref() {
    # Try the ref as-is, then fall back to origin/<ref>.
    local ref="$1"
    if git -C "$REPO" rev-parse --verify --quiet "$ref" >/dev/null; then
        echo "$ref"
    elif git -C "$REPO" rev-parse --verify --quiet "origin/$ref" >/dev/null; then
        echo "origin/$ref"
    else
        echo "ERROR: cannot resolve ref '$ref'" >&2
        return 1
    fi
}

latest_release_tag() {
    # Latest FIREFOX_<major>_*esr_RELEASE tag that is an ancestor of <ref>.
    local major="$1" ref="$2"
    git -C "$REPO" describe --tags --abbrev=0 \
        --match="FIREFOX_${major}_*esr_RELEASE" "$ref"
}

relbranch_for_tag() {
    # FIREFOX_140_10_0esr_RELEASE -> FIREFOX_ESR_140_10_X_RELBRANCH
    # FIREFOX_140_10_1esr_RELEASE -> FIREFOX_ESR_140_10_X_RELBRANCH
    local tag="$1"
    if [[ "$tag" =~ ^FIREFOX_([0-9]+)_([0-9]+)_[0-9]+esr_RELEASE$ ]]; then
        echo "FIREFOX_ESR_${BASH_REMATCH[1]}_${BASH_REMATCH[2]}_X_RELBRANCH"
    else
        echo "ERROR: unrecognized tag format '$tag'" >&2
        return 1
    fi
}

bugs_in_range() {
    # Print sorted-unique bug numbers from commit subjects in <tag>..<ref>.
    # Tolerates ranges with no bug-tagged commits (grep would otherwise exit 1).
    local tag="$1" ref="$2"
    git -C "$REPO" log --format='%s' "$tag..$ref" \
        | { grep -oiE 'bug[[:space:]]+[0-9]+' || true; } \
        | { grep -oE '[0-9]+' || true; } \
        | sort -u
}

run_one() {
    local version="$1"
    local branch tag relbranch
    branch=$(resolve_ref "origin/esr$version")
    tag=$(latest_release_tag "$version" "$branch")
    relbranch=$(resolve_ref "$(relbranch_for_tag "$tag")")

    echo "=== esr$version ==="
    echo "branch:    $branch"
    echo "tag:       $tag"
    echo "relbranch: $relbranch"

    local branch_bugs relbranch_bugs diff_bugs
    branch_bugs=$(bugs_in_range "$tag" "$branch")
    relbranch_bugs=$(bugs_in_range "$tag" "$relbranch")
    # Bugs on branch but not on relbranch.
    diff_bugs=$(comm -23 <(echo "$branch_bugs") <(echo "$relbranch_bugs"))

    if [[ -z "$diff_bugs" ]]; then
        echo "No bugs on $branch that are missing from $relbranch."
        echo
        return 0
    fi

    local count csv
    count=$(echo "$diff_bugs" | wc -l | tr -d ' ')
    csv=$(echo "$diff_bugs" | paste -sd, -)

    echo "Bugs on $branch but not on $relbranch ($count):"
    echo "$diff_bugs"
    echo
    echo "Bugzilla: $BUGZILLA_BASE?bug_id=$csv"
    echo
}

if [[ ! -d "$REPO/.git" && ! -f "$REPO/.git" ]]; then
    echo "ERROR: $REPO is not a git repo (set FIREFOX_REPO)" >&2
    exit 1
fi

if [[ $# -eq 0 ]]; then
    set -- "${DEFAULT_VERSIONS[@]}"
fi

for v in "$@"; do
    run_one "$v"
done
