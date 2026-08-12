#!/usr/bin/env python3
"""Check the skills and reference docs against what the scripts actually accept.

Two directions, and the second is the one that keeps finding things:

1. **Stale**: a doc names a flag no script takes. This goes wrong when a flag is renamed or removed
   and the prose that described it is left behind.
2. **Undocumented**: a script takes a flag, or `watchlist.py` has a subcommand, that appears in no
   doc. Every capability added in one recent week -- `--check-url`, `--comment last:N`,
   `list --status`, `--note` on the status transitions -- needed a separate nudge to get written
   down, and a capability nobody knows about is one that gets rebuilt inline.

Truth comes from `--help` rather than from parsing argparse calls, so it reflects what the parser
really accepts, including every subcommand's own options.

Two things make the difference between this being useful and being decorative, both learned by
getting them wrong first:

- **Attribution coverage.** Matching a flag to "the script named on the same line" covered 38% of
  mentions; most doc references are prose, in backticks, with no command on the line. Checking
  against the *union* of every script's flags covers all of them, at the cost of not catching a flag
  named against the wrong script. That trade is deliberate: a flag that exists somewhere is a far
  smaller problem than one that exists nowhere.
- **Other tools' flags.** Docs are full of `git log --grep`, `curl -s`, `head -n`. Eight of the
  first run's nine "failures" were git flags. Lines invoking another tool are skipped.

Read-only. Exits non-zero if anything is stale, so it can gate a commit.

Usage:
  doc-flag-audit.py
  doc-flag-audit.py --show-plumbing    # include the mechanical options in the undocumented list
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO / "scripts" / "relnotes"
DOC_GLOBS = (".claude/skills/*/SKILL.md", "reference/release-notes/*.md", "README.md")

FLAG_RE = re.compile(r"--[a-z][\w-]*")
# Maintenance tools, not part of a pass. See truth().
AUDITORS = {"doc-flag-audit.py", "doc-command-audit.py"}
# Lines that invoke one of these are describing that tool's flags, not ours.
OTHER_TOOLS = ("git", "curl", "grep", "sed", "awk", "head", "tail", "jq", "chmod", "python3 -c")
# The shortest flag any parser here accepts is 5 characters (`--all`, `--due`, `--rev`), so a shorter
# token that matches nothing is not a flag: CSS custom properties appear inside quoted release notes.
# The condition is deliberately the conjunction rather than a length rule alone -- a short token that
# *is* a real flag still gets checked, so adding a `--id` later cannot silently stop it being audited.
MIN_FLAG_LEN = 5
# Mechanical options: real, but documenting them adds noise rather than capability.
PLUMBING = {
    "--help", "--no-fetch", "--output", "--workdir", "--refresh", "--limit", "--width",
    "--verbose", "--all-releases", "--repo", "--format", "--outdir", "--min-cluster",
    "--max-meta-deps", "--max-parents", "--max-path-cluster", "--months", "--date",
    "--per-build", "--channels", "--platforms", "--skip-flags", "--include-dropped",
    # Passed by daily-pass to scan-window, never by a person: the drop file it produces is what
    # the skill documents, not the flag that puts it there.
    "--dropped-out",
}


def flags_of(*argv: str) -> set:
    """Flags a parser accepts, read from its own --help."""
    r = subprocess.run([sys.executable, *argv, "--help"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"# WARNING: {' '.join(argv)} --help exited {r.returncode}; its flags are unchecked",
              file=sys.stderr)
    return set(FLAG_RE.findall(r.stdout))


def truth() -> tuple[dict, list]:
    """{script or 'watchlist.py <sub>': flags}, plus the subcommand names.

    The auditors are excluded: the docs checked here are the skills and reference notes a
    release-note pass reads, and a maintenance tool does not belong in them. Left in, each would
    report its own flags forever, which is how a report gets skimmed.
    """
    table = {p.name: flags_of(str(p)) for p in sorted(SCRIPT_DIR.glob("*.py"))
             if p.name not in AUDITORS}
    wl = SCRIPT_DIR / "watchlist.py"
    usage = subprocess.run([sys.executable, str(wl), "--help"],
                           capture_output=True, text=True).stdout
    # The subcommand list prints as {add,note,...,check-setup,...}; names carry hyphens.
    subs = sorted(set(re.findall(r"[\{,]([a-z][a-z\-]*)(?=[,\}])", usage)))
    for s in subs:
        table[f"watchlist.py {s}"] = flags_of(str(wl), s)
    return table, subs


def main() -> None:
    p = argparse.ArgumentParser(description="Audit doc flag references against --help output.")
    p.add_argument("--show-plumbing", action="store_true",
                   help="also list undocumented mechanical options (--no-fetch, --output, ...)")
    args = p.parse_args()

    table, subs = truth()
    every_flag = set().union(*table.values())
    docs = [q for g in DOC_GLOBS for q in sorted(REPO.glob(g))]
    if not docs:
        sys.exit(f"error: no docs matched {DOC_GLOBS} under {REPO}")

    stale, mentioned, checked = [], set(), 0
    for doc in docs:
        rel = doc.relative_to(REPO)
        for i, line in enumerate(doc.read_text().splitlines(), 1):
            other = next((t for t in OTHER_TOOLS if re.search(rf"\b{re.escape(t)}\b", line)), None)
            for flag in FLAG_RE.findall(line):
                if len(flag) < MIN_FLAG_LEN and flag not in every_flag:
                    continue
                mentioned.add(flag)
                if other:
                    continue
                checked += 1
                if flag not in every_flag:
                    stale.append((f"{rel}:{i}", flag, line.strip()[:70]))

    print(f"{len(docs)} doc(s), {len(table)} parsers ({len(subs)} watchlist subcommands), "
          f"{checked} flag mention(s) checked\n")
    print(f"=== STALE -- named in a doc, accepted by nothing ({len(stale)})")
    for where, flag, text in stale:
        print(f"  {where:<52} {flag:<18} {text}")
    if not stale:
        print("  none")

    undoc = {}
    for name, flags in table.items():
        missing = sorted(f for f in flags - mentioned
                         if args.show_plumbing or f not in PLUMBING)
        if missing:
            undoc[name] = missing
    print(f"\n=== UNDOCUMENTED -- accepted, named in no doc ({sum(len(v) for v in undoc.values())})")
    for name, missing in sorted(undoc.items()):
        print(f"  {name:<28} {', '.join(missing)}")
    if not undoc:
        print("  none")

    doc_text = "\n".join(d.read_text() for d in docs)
    orphan = [s for s in subs if not re.search(rf"watchlist\.py[^\n]*\b{re.escape(s)}\b", doc_text)]
    print(f"\n=== SUBCOMMANDS never named in any doc ({len(orphan)})")
    print("  " + (", ".join(orphan) if orphan else "none"))

    sys.exit(1 if stale else 0)


if __name__ == "__main__":
    main()
