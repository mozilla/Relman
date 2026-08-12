#!/usr/bin/env python3
"""Run every `watchlist.py` invocation the docs show, and report the ones that don't work.

Companion to `doc-flag-audit.py`, which checks that documented *flags* exist. A flag can exist and
the command still be wrong advice: the docs offered `watchlist.py decline <bug> --note "<why>"` as
the way to record a verdict for a year, and it exits 1 every time, because a bug judged during a
pass has never been tracked and `decline` only moves an existing entry. Three rounds of human
review and a flag audit all missed that; running the command found it immediately.

**Cold versus seeded is the whole design.** Some documented commands legitimately need prior state
-- the status transitions are *supposed* to require an entry. So each one runs twice: once against
an empty state directory, and if that fails, again against a state directory seeded with an `add`
for the same key. That separates two causes mechanically instead of carrying an exclusion list that
grows by one every time someone documents a transition:

    ok           works from nothing
    needs-state  fails cold, works seeded -- correct for a status transition, and worth knowing
                 the docs are showing a command that assumes an earlier pass ran
    no-clone     needs a Gecko checkout that this machine has not got, so unjudgeable here
    seed-failed  the seed itself would not run, so the verdict is this audit's fault, not the doc's
    BROKEN       fails both ways -- the documented command is wrong

BROKEN and seed-failed exit non-zero; a verdict that could not be reached must not read like a
clean one. `no-clone` does not, because it says something about the machine rather than the docs.

Scope is `watchlist.py` on purpose: it owns the per-user state, so cold-start failures live there,
and every other script either reaches the network or needs the Gecko clone, which would make this
slow and flaky for no extra coverage. Skipped invocations are counted and listed rather than
dropped quietly -- an audit that silently covers half the docs reads exactly like one that passes.

State goes to a throwaway `XDG_STATE_HOME` per invocation, so a run cannot touch the real watchlist
or watermark. The Gecko clone is the one thing passed through from the real config, because
isolating it would only test whether the legacy path guess happens to be right here.

Usage:
  doc-command-audit.py
  doc-command-audit.py -v      # show every invocation, not just the problems
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
WATCHLIST = REPO / "scripts" / "relnotes" / "watchlist.py"
DOC_GLOBS = (".claude/skills/*/SKILL.md", "reference/release-notes/*.md", "README.md")
# Only the watchlist state is isolated; the Gecko clone is handed to the children explicitly.
# Without this a child finds no config.json in the throwaway state directory and falls through
# resolve_repo to the legacy ~/repos/firefox guess -- correct on the machine that guess was written
# for and wrong on every other, which would turn "is this command documented correctly" into "is
# your clone in the usual place" and exit 1 accordingly.
CLONE = trainlib.read_config().get("gecko_repo", "")
# Some invocations (`resume`) need the clone at all. When none is resolvable this audit cannot
# judge them either way, and the sentinel below is how the resolver says so -- classified apart
# from a broken command, because "your machine has no clone yet" must not read as "the docs are
# wrong", and must not gate a commit on it. `check-setup` is the fix and it says so itself.
HAVE_CLONE = bool(CLONE) or trainlib.is_gecko_checkout(trainlib.LEGACY_REPO_DEFAULT)
NO_CLONE_SENTINEL = "could not locate the Gecko checkout"

INVOCATION_RE = re.compile(r"\bwatchlist\.py\s+([a-z][a-z-]*)([^`\n]*)")
# Placeholders the docs use, with a value that satisfies the parser. A bug id that exists is used
# for `<bug>` so `add` records something plausible; nothing here reaches Bugzilla.
PLACEHOLDERS = {
    "<bug>": "2051691", "<ids>": "2051691", "<why>": "audit probe",
    "<what and when>": "audit probe", "<where it shipped>": "audit probe",
    "<text>": "audit probe", "<pass summary>": "audit probe", "<s>": "asked",
    "<YYYYMMDD>": "20260801", "<gecko clone>": "", "<path>": "",
    "<short description>": "audit probe", "<date>": "2026-08-01",
}
# Options that write outside the throwaway state directory, so the audit must not execute them:
# `--pull` fast-forwards the real checkout and `--write` edits settings.local.json. This is a
# category (escapes the sandbox), not a list of individual awkward commands.
ESCAPES_SANDBOX = ("--pull", "--write")


def logical_lines(text: str) -> list[tuple[int, str]]:
    """(first line number, line) with backslash continuations folded into one line.

    A command split across lines is one command. Running only its first physical line executes an
    invocation nobody wrote, and the docs contain exactly that shape -- an `add ... --release 155 \\`
    whose `--note` lives on the next line. Folding tests what is written; truncating reported a pass
    for something that was never run.
    """
    out: list[tuple[int, str]] = []
    pending: list[str] = []
    start = 0
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not pending:
            start = i
        if line.endswith("\\"):
            pending.append(line[:-1].rstrip())
            continue
        out.append((start, " ".join(pending + [line.strip()])) if pending else (i, raw))
        pending = []
    if pending:
        out.append((start, " ".join(pending)))
    return out


def documented() -> tuple[dict, list]:
    """{(subcommand, argument string): "file:line"}, plus what was skipped and why."""
    found: dict = {}
    skipped = []
    for glob in DOC_GLOBS:
        for doc in sorted(REPO.glob(glob)):
            rel = doc.relative_to(REPO)
            for i, line in logical_lines(doc.read_text()):
                m = INVOCATION_RE.search(line)
                if not m:
                    continue
                where = f"{rel}:{i}"
                sub = m.group(1)
                # Trailing `# ...` is documentation of the command, not an argument to it. Missing
                # this reported 12 of 15 invocations as broken on the first run, all of them fine.
                #
                # Strip it *before* the checks below, not after. A comment can contain a `|`
                # (`# asked | declined | gated`), and testing the raw line for one skipped two of
                # the most consequential invocations in the docs while the run still printed
                # "0 broken" -- a clean result standing on coverage it did not have.
                rest = m.group(2).split(" #")[0].strip()
                # A markdown table row or a prose bullet describes a command rather than quoting a
                # runnable one, and a pipeline is not a bare invocation; running either tests the
                # extractor instead of the docs.
                stripped = line.strip()
                if stripped.startswith(("|", "- ", "* ", "**", "#")):
                    skipped.append((where, f"{sub} {rest}", "prose or table, not a command line"))
                    continue
                if "|" in rest:
                    skipped.append((where, f"{sub} {rest}", "shell pipeline, not a bare invocation"))
                    continue
                for token, value in PLACEHOLDERS.items():
                    rest = rest.replace(token, value)
                if "<" in rest:
                    skipped.append((where, f"{sub} {rest}", "unsubstituted placeholder"))
                    continue
                if any(opt in rest for opt in ESCAPES_SANDBOX):
                    skipped.append((where, f"{sub} {rest}", "would write outside the sandbox"))
                    continue
                try:
                    argv = shlex.split(rest)
                except ValueError as e:
                    skipped.append((where, f"{sub} {rest}", f"unparseable ({e})"))
                    continue
                found.setdefault((sub, tuple(argv)), where)
    return found, skipped


def seed_args(argv: tuple, key: str) -> list:
    """`add` arguments that create the entry the invocation under test will look for.

    The release comes from the invocation itself when it names one. Seeding a fixed release while
    the command asks for a different one would report a correct command as broken, and pinning a
    literal here would start doing that the first time a doc example moves to the next cycle.
    """
    out = ["add", key, "--status", "watching", "--note", "audit seed"]
    if "--release" in argv:
        i = argv.index("--release")
        if i + 1 < len(argv):
            out += ["--release", argv[i + 1]]
    return out


def run(sub: str, argv: tuple, parent: Path, seed: str | None = None) -> dict:
    """Run one documented invocation in its own throwaway state directory.

    A fresh directory per call rather than one path reused and cleared: the cold/seeded distinction
    is the whole classification, and it must not depend on a delete having succeeded. Cleanup is
    best-effort precisely because nothing is riding on it.

    Returns {"rc", "err", "seeded"} -- `seeded` False means the seed itself failed, which is an
    audit malfunction and has to be told apart from the command under test failing.
    """
    state = Path(tempfile.mkdtemp(dir=parent))
    env = dict(os.environ, XDG_STATE_HOME=str(state))
    if CLONE:
        env["RELMAN_GECKO_REPO"] = CLONE
    base = [sys.executable, str(WATCHLIST)]

    def invoke(args: list, timeout: int) -> dict:
        try:
            r = subprocess.run(base + args, capture_output=True, text=True, env=env, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            # Reported rather than raised: one hanging documented command would otherwise end the
            # audit in a traceback, losing the verdict on every invocation after it.
            return {"rc": -1, "err": f"timed out after {e.timeout}s", "seeded": True}
        return {"rc": r.returncode, "err": (r.stderr or r.stdout).strip(), "seeded": True}

    try:
        if seed:
            s = invoke(seed_args(argv, seed), 120)
            if s["rc"] != 0:
                return {**s, "seeded": False}
        return invoke([sub, *argv], 300)
    finally:
        shutil.rmtree(state, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Execute documented watchlist.py invocations.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="list every invocation, not only the ones needing attention")
    args = p.parse_args()

    if not HAVE_CLONE:
        print("# WARNING: no Gecko clone is resolvable, so invocations needing one cannot be "
              "judged; they are reported as no-clone. Run watchlist.py check-setup --repo <path>.",
              file=sys.stderr)
    found, skipped = documented()
    if not found:
        sys.exit(f"error: no watchlist.py invocations found in {DOC_GLOBS} under {REPO}")

    tmp = Path(tempfile.mkdtemp(prefix="doc-command-audit-"))
    results = []
    try:
        for (sub, argv), where in sorted(found.items(), key=lambda kv: kv[1]):
            cold = run(sub, argv, tmp)
            if cold["rc"] == 0:
                results.append(("ok", where, sub, argv, ""))
                continue
            # Seed the key the command itself names -- its first positional, whatever its shape.
            # Matching only digits would seed the wrong entry for a feature key like
            # `skpdf-print-backend` and then call a correct command broken.
            key = next((a for a in argv if not a.startswith("-")), "2051691")
            seeded = run(sub, argv, tmp, seed=key)
            first = cold["err"].splitlines()[0][:100] if cold["err"] else ""
            if NO_CLONE_SENTINEL in cold["err"]:
                results.append(("no-clone", where, sub, argv, first))
            elif not seeded["seeded"]:
                results.append(("seed-failed", where, sub, argv,
                                f"could not seed: {seeded['err'].splitlines()[0][:80]}"))
            else:
                results.append(("needs-state" if seeded["rc"] == 0 else "BROKEN",
                                where, sub, argv, first))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    LABELS = ("ok", "needs-state", "no-clone", "seed-failed", "BROKEN")
    counts = {k: sum(1 for r in results if r[0] == k) for k in LABELS}
    print(f"{len(results)} documented invocation(s) executed, {len(skipped)} skipped — "
          + ", ".join(f"{counts[k]} {k}" for k in LABELS) + "\n")

    for label in ("BROKEN", "seed-failed", "no-clone", "needs-state", "ok"):
        rows = [r for r in results if r[0] == label]
        if label == "ok" and not args.verbose:
            continue
        print(f"=== {label} ({len(rows)})")
        for _, where, sub, argv, err in rows:
            print(f"  {where:<22} watchlist.py {sub} {' '.join(argv)[:52]}")
            if err:
                print(f"      {err}")
        if not rows:
            print("  none")
        print()

    if args.verbose or skipped:
        print(f"=== SKIPPED ({len(skipped)})")
        for where, text, why in skipped if args.verbose else skipped[:8]:
            print(f"  {where:<22} {text[:46]:<46} {why}")
        if not args.verbose and len(skipped) > 8:
            print(f"  ... and {len(skipped) - 8} more (-v for all)")

    # seed-failed is a malfunction of this audit rather than a doc bug, and it still exits non-zero:
    # a check that could not reach a verdict must not read like one that came back clean.
    sys.exit(1 if counts["BROKEN"] or counts["seed-failed"] else 0)


if __name__ == "__main__":
    main()
