#!/usr/bin/env bash
# Regenerate the README screenshots, in a dark and a light version.
#
#   docs/screenshots.sh TASK_ID [TASK_ID ...]
#
# The task IDs are the task groups shown in the tk.py example. Each command
# runs once, in a pseudo-terminal so it prints colors, and the same output is
# used for both SVGs and printed as plain text for the README's
# "Same output as text" blocks.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
    echo "usage: docs/screenshots.sh TASK_ID [TASK_ID ...]" >&2
    exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

shot() {
    local name=$1 title=$2 shown=$3 run=$4
    # 2>/dev/null keeps the tk.py progress spinner out of the capture
    script -qc "$run 2>/dev/null" /dev/null > "$tmp/$name.ansi"
    for theme in dark light; do
        docs/ansi2svg.py --chrome gnome --theme "github-$theme" --title "$title" \
            --command "$shown" < "$tmp/$name.ansi" > "docs/$name-$theme.svg"
    done
    echo "===== $name (text for the README)"
    echo "\$ $shown"
    sed -e 's/\x1b\[[0-9;]*m//g' -e 's/\r$//' "$tmp/$name.ansi"
}

shot firefox_versions "firefox_versions.py" "./firefox_versions.py" "./firefox_versions.py"
shot tk "tk.py" "./tk.py $*" "./tk.py $*"
