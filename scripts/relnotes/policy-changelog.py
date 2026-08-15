#!/usr/bin/env python3
"""What enterprise policies changed in a Firefox version, from the policy-templates changelog.

Release Management absorbed enterprise release notes in August 2026, when the separate Firefox for
Enterprise notes stopped being maintained. The policy templates themselves are still maintained, and
each release names that version's new and updated policies with a one-line description. That makes it
two things at once:

1. **A completeness check.** The changelog is authored independently of our funnel, so a policy in it
   that our pass never surfaced is a real miss -- the same role the Bugzilla census plays for the
   commit window. Worth having because the flag queue is empty for this class: every FIXED
   `Enterprise Policies` bug measured in a year carried `relnote-firefox = ---`, including one that
   shipped a published note, so "the developer nominates it themselves" does not hold here.

2. **Drafting material.** The descriptions are written for administrators, which is the audience of
   the note. Fuller reference documentation is at https://firefox-admin-docs.mozilla.org/.

**This runs at end of cycle or before notes are finalized, not during a Nightly pass.** A template
release ships alongside the Firefox release, so for the version currently in Nightly there is
legitimately nothing published yet. That case prints a message and exits non-zero rather than
reporting an empty policy list, which would read as "no policy changes this cycle". During the cycle
the `[enterprise policy]` label in `scan-window.py` is what covers this class.

**One Firefox version can have several template releases** -- a dot release gets its own. They are
merged here: Firefox 149 shipped v7.9 with no new policies and v7.9.1 with two, so reading only the
first would report none.

Bug mapping is **best effort and one-directional evidence**. A policy name matched to a bug is worth
reading; nothing found means the name does not appear in a bug summary or in the commits touching
the policy directory, not that no bug exists. Two channels, because policy names are rarely quoted
verbatim in a bug summary ("Add a policy for Windows launch on login" is how one reads): Bugzilla
summary search first, then `git log -S<name>` over the enterprise policy directory, which finds the
commit that introduced the identifier and takes the bug from its subject.

**Both channels are scoped to the version**, and that is what makes them usable rather than
misleading. An established policy accumulates bugs across years, so searching its name alone returns
a confidently wrong bug for every *updated* policy -- `Homepage` matched a Weather preference and
`Containers` a per-site association feature before the scope was added. A wrong bug link is worse
than none, because nothing about it looks uncertain.

**Bug mapping needs a Gecko clone**, so without one this exits with the same setup instructions every
other script here gives. `--no-map` is the way to run without a clone; it reports the policies and
their descriptions and says that nothing in the output speaks to whether a note was asked for.

Usage:
  policy-changelog.py                          # the current release
  policy-changelog.py --version 153
  policy-changelog.py --list                   # every template release and the version it covers
  policy-changelog.py --version 153 --no-map   # skip bug mapping (no clone or Bugzilla needed)
  policy-changelog.py --version 153 --format json
"""

import argparse
import collections
import json
import re
import sys
import urllib.parse as url_parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

RELEASES_PER_PAGE = 100
RELEASES = ("https://api.github.com/repos/mozilla/policy-templates/releases"
            f"?per_page={RELEASES_PER_PAGE}")
BUGZILLA_REST = "https://bugzilla.mozilla.org/rest/bug"
ADMIN_DOCS = "https://firefox-admin-docs.mozilla.org/"
# scan-window's SHIPPED_FLAGS, not its LANDED_IN_VERSION: that one also counts `disabled`, and a
# policy the changelog announces did ship, so a disabled flag on a same-named bug is a different
# change.
SHIPPED_IN_VERSION = ("fixed", "verified")
# How many matches one policy entry lists before it stops. The *count* is always reported separately,
# because a truncated listing whose length reads as the total is how "4 candidate bugs" came to stand
# for 32 of them.
MATCHES_SHOWN = 4
# Ambiguity is the answer here, not a bug list, so this only has to be wide enough to distinguish a
# homonym from a real cluster -- and small enough that saturating it is rare and reported.
BUGZILLA_MATCH_LIMIT = 25

# "Policy templates for Firefox 153 and Firefox ESR 153" -- the ESR number must not win, so this
# anchors on the first "Firefox <n>" after "for". Dot releases ("Firefox 149.0.2") map to the major.
NAME_VERSION_RE = re.compile(r"for\s+Firefox\s+(\d+)", re.IGNORECASE)
# Sections are `### New policies`, `### Updated policies`; wording has varied, so match the shape.
SECTION_RE = re.compile(r"^###\s+(new|updated|changed|removed|deprecated)\s+polic\w*\s*$",
                        re.IGNORECASE | re.MULTILINE)
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# One policy entry. The bullet marker is optional because the changelog's own formatting varies:
# v8.0 writes `* [Name](url) - description`, v7.9.1 writes the same line with no bullet at all, and
# requiring one silently dropped both policies that version added. Anchoring on the *name* instead --
# a Markdown link, or an identifier followed by a dash -- reads both forms and still refuses the
# prose paragraphs that share these sections.
ENTRY_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\[(?P<link>[^\]]+)\]\([^)]*\)|(?P<bare>[A-Za-z][A-Za-z0-9_]*)\s*[-–—])")
# Real names are CamelCase with the occasional digit; strip any trailing punctuation the link carries.
NAME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)")
BUG_IN_SUBJECT_RE = re.compile(r"\bBug\s+(\d{6,7})\b", re.IGNORECASE)


def all_releases() -> list[dict]:
    """Every published template release, newest first.

    A single page today, and the count is close enough to the cap to be worth saying so: once it is
    exceeded the oldest releases stop coming back, and the "no release covers Firefox N" exit below
    would blame an unpublished changelog for a paging limit.
    """
    rels = trainlib.fetch_json(RELEASES)
    if len(rels) >= RELEASES_PER_PAGE:
        print(f"# WARNING: {len(rels)} releases returned, at the per-page cap -- older releases are "
              "probably missing, so 'no release covers <version>' is unreliable for old versions",
              file=sys.stderr)
    return rels


def releases_for(version: int, rels: list[dict]) -> list[dict]:
    """Every template release covering a Firefox major, newest first."""
    out = []
    for r in rels:
        m = NAME_VERSION_RE.search(r.get("name") or "")
        if m and int(m.group(1)) == version:
            out.append(r)
    return out


def parse_policies(body: str) -> tuple[dict, list]:
    """({section: [{name, text}]}, unparsed lines) from a release body.

    The unparsed lines are returned rather than dropped because this parser has already lost real
    policies once: v7.9.1 writes its entries with no bullet marker, and both policies Firefox 149
    added went missing silently. The changelog's formatting is not a contract, so the next variation
    has to be visible.
    """
    sections: dict[str, list] = collections.OrderedDict()
    skipped: list[str] = []
    parts = SECTION_RE.split(body or "")
    # split() yields [preamble, kind, chunk, kind, chunk, ...]
    for kind, chunk in zip(parts[1::2], parts[2::2]):
        items = []
        for line in chunk.splitlines():
            if line.startswith("#"):
                break
            m = ENTRY_RE.match(line)
            if not m:
                if line.strip():
                    skipped.append(line.strip())
                continue
            text = MD_LINK_RE.sub(r"\1", line.strip().lstrip("-* ")).replace("`", "")
            raw = m.group("link") or m.group("bare")
            name = NAME_RE.match(raw)
            items.append({"name": name.group(1) if name else None, "text": text})
        if items:
            sections.setdefault(kind.lower(), []).extend(items)
    return sections, skipped


def bug_from_bugzilla(name: str, version: int) -> dict | None:
    """A bug that quotes this policy name AND shipped in this version.

    The version scope is not a refinement, it is what makes the answer usable. An existing policy
    accumulates bugs -- `Containers` matches a per-site association feature, `Homepage` matches a
    Weather preference -- so an unscoped search returns a confidently wrong bug for every *updated*
    policy, which is worse than returning none.
    """
    qs = url_parse.urlencode({
        "short_desc": name,
        "short_desc_type": "casesubstring",
        "f1": f"cf_status_firefox{version}",
        "o1": "anyexact",
        "v1": ",".join(SHIPPED_IN_VERSION),
        "include_fields": "id,summary,status,resolution,cf_tracking_firefox_relnote",
        "order": "bug_id DESC",
        "limit": BUGZILLA_MATCH_LIMIT,
    })
    try:
        bugs = trainlib.fetch_json(f"{BUGZILLA_REST}?{qs}").get("bugs", [])
    except RuntimeError as e:
        print(f"# WARNING: Bugzilla lookup for {name} failed ({e}); it is unmapped, not unbugged",
              file=sys.stderr)
        return None
    if not bugs:
        return None
    if len(bugs) > 1:
        # Policy names are often ordinary words -- Containers, Homepage, Preferences -- so several
        # unrelated bugs in one version quote them. Naming one of them would be a guess wearing the
        # costume of a lookup, so hand back the ambiguity instead.
        return {"ambiguous": [b["id"] for b in bugs[:MATCHES_SHOWN]], "via": "bugzilla",
                "total": len(bugs), "total_is_floor": len(bugs) >= BUGZILLA_MATCH_LIMIT}
    b = dict(bugs[0])
    b["via"] = "bugzilla"
    return b


def bug_from_tree(repo: Path, name: str, span: tuple | None) -> dict | None:
    """Bugs whose commits changed a line naming this policy under the policy directory.

    Scoped to the version's cycle for the same reason as the Bugzilla channel: unbounded, `-S` finds
    the commit that first added the name years ago, which is the wrong bug for an updated policy.

    Several bugs legitimately touch one policy *in a cycle*, and unlike the Bugzilla channel's
    homonyms those are all real evidence, so they are all reported -- newest first, since the
    changelog entry describes where the policy ended up. Across all history they are not: a name like
    `Preferences` or `Homepage` appears throughout the tree, so an **unbounded** multi-hit is the
    absence of a signal and is handed back as an ambiguity. That test is on the search being unbounded
    rather than on which caller made it, because `span` is also None when the checkout simply has no
    cycle tag -- and then the first search is unbounded too.
    """
    rev = f"{span[0]}..{span[1]}" if span else trainlib.gecko_upstream()
    rc, out, err = trainlib.git_rc(repo, "log", rev, f"-S{name}", "--format=%s", "--",
                                   *trainlib.ENTERPRISE_POLICY_DIRS)
    if rc != 0:
        # Not the same answer as "found nothing": a bad `gecko_upstream` ref fails here, and reporting
        # it as unmapped would read as evidence that no bug exists.
        print(f"# WARNING: tree lookup for {name} failed at {rev} (git exited {rc}: "
              f"{err.strip()[:120]}); it is unmapped, not unbugged", file=sys.stderr)
        return None
    via = "tree" if span else "tree, any cycle"
    landings, seen = [], set()
    for subject in out.splitlines():
        m = BUG_IN_SUBJECT_RE.search(subject)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            landings.append({"id": int(m.group(1)), "subject": subject.strip()})
    if not landings:
        return None
    if span is None and len(landings) > 1:
        return {"via": via, "total": len(landings),
                "ambiguous": [x["id"] for x in landings[:MATCHES_SHOWN]]}
    return {**landings[0], "via": via, "total": len(landings),
            "more": landings[1:MATCHES_SHOWN]}


def annotate(sections: dict, version: int, repo: Path) -> None:
    """Attach a best-effort bug to each policy, in place."""
    span = trainlib.cycle_range(repo, version)
    if span is None:
        print(f"# WARNING: no FIREFOX_NIGHTLY_{version - 1}_END tag in this checkout, so the tree "
              "lookup runs unbounded and may name the commit that first added a policy rather than "
              "the one that changed it", file=sys.stderr)
    elif span[2]:
        print(f"# NOTE: Firefox {version}'s cycle has no closing tag here, so the tree lookup "
              "runs to HEAD", file=sys.stderr)
    for items in sections.values():
        for item in items:
            name = item["name"]
            if not name:
                continue
            # Tree first: it requires the identifier to have *changed* in the policy code this
            # cycle, which is the claim being made. Bugzilla's summary search is the looser channel
            # and only gets asked when the tree has nothing.
            found = bug_from_tree(repo, name, span)
            if found is None:
                found = bug_from_bugzilla(name, version)
            # Last resort, and labelled as such: the commit that first introduced the identifier at
            # all. For a genuinely new policy that is the bug; for an updated one it is the wrong
            # cycle, which is why it only runs once the scoped channels have both come back empty.
            # bug_from_tree collapses an unbounded multi-hit to an ambiguity itself.
            if found is None and span is not None:
                found = bug_from_tree(repo, name, None)
            item["bug"] = found


def render(version: int, rels: list[dict], sections: dict, mapped: bool) -> list[str]:
    lines = []
    for r in rels:
        lines.append(f"Firefox {version}: {r['name']}  ({r['tag_name']}, "
                     f"published {(r.get('published_at') or '')[:10]})")
        lines.append(f"  {r['html_url']}")
    lines.append("")
    if not sections:
        lines.append(f"No policy changes recorded for Firefox {version}. The release(s) above carry "
                     "no new or updated policy section, which is a normal cycle rather than a "
                     "missing changelog -- a run of versions ships none.")
        return lines
    unmapped = []
    for kind, items in sections.items():
        lines.append(f"{kind.upper()} POLICIES ({len(items)})")
        for item in items:
            lines.append(f"  {item['text']}")
            bug = item.get("bug")
            if bug and bug.get("ambiguous"):
                shown, total = bug["ambiguous"], bug.get("total", len(bug["ambiguous"]))
                floor = "at least " if bug.get("total_is_floor") else ""
                clip = f", first {len(shown)} shown" if total > len(shown) else ""
                lines.append(f"      {floor}{total} candidate bugs, none singled out "
                             f"(via {bug['via']}){clip}: {', '.join(str(i) for i in shown)}")
            elif bug:
                flag = bug.get("cf_tracking_firefox_relnote")
                flag = f"  relnote-firefox={flag}" if flag else ""
                # `summary` is the bug's, `subject` a commit's -- the tree channel only ever sees the
                # latter, and they are separate keys so a JSON consumer can tell which it has.
                text = bug.get("summary") or bug.get("subject") or ""
                lines.append(f"      bug {bug['id']} (via {bug['via']}){flag}  {text[:90]}")
                for other in bug.get("more") or []:
                    lines.append(f"      bug {other['id']} (via {bug['via']})  "
                                 f"{other['subject'][:90]}")
                hidden = bug.get("total", 1) - 1 - len(bug.get("more") or [])
                if hidden > 0:
                    lines.append(f"      ... and {hidden} more bug(s) touching this policy")
            elif mapped and item["name"]:
                unmapped.append(item["name"])
                lines.append("      no bug found by name -- check by hand before treating it as new")
        lines.append("")
    lines.append(f"Descriptions above are the administrator-facing wording; fuller reference at "
                 f"{ADMIN_DOCS}")
    if unmapped:
        lines.append(f"Unmapped ({len(unmapped)}): {', '.join(unmapped)} -- policy names are often "
                     "absent from bug summaries, so this is a prompt to look, not a gap.")
    if not mapped:
        lines.append("Bug mapping skipped (--no-map), so nothing here says whether a note was asked "
                     "for.")
    return lines


def main() -> None:
    p = argparse.ArgumentParser(
        description="New and updated enterprise policies for a Firefox version.")
    p.add_argument("--version", type=int,
                   help="Firefox major version (default: the current release)")
    p.add_argument("--list", action="store_true",
                   help="list every template release and the Firefox version it covers")
    p.add_argument("--no-map", action="store_true",
                   help="skip Bugzilla and tree lookups for each policy")
    p.add_argument("--repo", help="path to a Gecko clone (default: the configured one)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    args = p.parse_args()

    if args.list:
        rows = []
        for r in all_releases():
            m = NAME_VERSION_RE.search(r.get("name") or "")
            rows.append((r["tag_name"], (r.get("published_at") or "")[:10],
                         m.group(1) if m else "?", r.get("name") or ""))
        if args.format == "json":
            print(json.dumps([{"tag": t, "published": w, "firefox": (None if v == "?" else int(v)),
                               "name": n} for t, w, v, n in rows], indent=2))
        else:
            print(f"{len(rows)} template release(s):")
            for tag, when, ver, name in rows:
                print(f"  {tag:<8} {when}  Firefox {ver:<4} {name[:60]}")
        unparsed = [t for t, _, v, _ in rows if v == "?"]
        if unparsed:
            print(f"\n# WARNING: could not read a Firefox version from {len(unparsed)} release "
                  f"name(s): {', '.join(unparsed)} -- --version cannot find those", file=sys.stderr)
        return

    version = args.version
    if version is None:
        version = trainlib.train_versions()["release"]
        if version is None:
            sys.exit("error: could not read the current release version; pass --version")
        print(f"# no --version given, using the current release ({version})", file=sys.stderr)

    rels = releases_for(version, all_releases())
    if not rels:
        # Deliberately an error: an empty policy list and an unpublished changelog are the same
        # output otherwise, and one of them means "we have not checked yet".
        sys.exit(f"error: no policy-templates release covers Firefox {version}. The templates ship "
                 f"with the release, so a version still on Nightly or Beta has none yet -- during "
                 f"the cycle use scan-window's [enterprise policy] label instead. `--list` shows "
                 f"what is published.")

    sections: dict[str, list] = collections.OrderedDict()
    for r in rels:
        parsed, skipped = parse_policies(r.get("body") or "")
        for kind, items in parsed.items():
            sections.setdefault(kind, []).extend(items)
        if skipped:
            print(f"# NOTE: {len(skipped)} line(s) in {r['tag_name']}'s policy sections were not read "
                  "as entries -- prose and credits look like this, a format change also would:",
                  file=sys.stderr)
            for line in skipped:
                print(f"#   {line[:100]}", file=sys.stderr)

    repo = None
    if not args.no_map and sections:
        # Exits with setup instructions when there is no clone, like every other script here. The
        # Bugzilla channel alone would work, but a partial mapping presented as the whole one is the
        # failure this file is otherwise careful to avoid -- `--no-map` is the way to run without one.
        repo = trainlib.resolve_repo(args.repo)
        annotate(sections, version, repo)

    if args.format == "json":
        print(json.dumps({
            "version": version,
            "releases": [{"tag": r["tag_name"], "name": r["name"],
                          "published": (r.get("published_at") or "")[:10],
                          "url": r["html_url"]} for r in rels],
            "policies": sections,
            "mapped": not args.no_map,
            "tooling": trainlib.tooling_stamp(),
        }, indent=2))
        return
    print("\n".join(render(version, rels, sections, not args.no_map)))


if __name__ == "__main__":
    main()
