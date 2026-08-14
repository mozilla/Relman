#!/usr/bin/env python3
"""Read a *rendered* release-notes page: sections, notes, bug links, and inline markup.

Companion to fetch-shipped-notes.py, not part of it. That script reads Nucleus JSON -- what was
published, as data -- and says so in its own docstring ("no HTML scraping of firefox.com required").
Reviewing a draft is the other question: what the reader will actually see on the page, in document
order, with the markup that Nucleus's JSON does not tell you about. A staging URL is the only place
that exists.

Exists because every review pass rebuilt it by hand. Four `python3 - <<'EOF'` snippets in one pass
re-derived the same regexes to answer "which notes use inline code", and inline Python prompts for
approval (measured 2026-08-09 -- see reference/release-notes/command-forms.md), so the check could
not run unattended. Same reasoning as bug-detail.py: a stable script under scripts/relnotes/ is
allowlisted by prefix once.

The code-formatting audit is deliberately narrow. It flags two shapes in prose -- a call with
parentheses, and a dotted identifier of three or more parts that is not a domain name -- and nothing
else. It was built for a real finding: on the 155.0a1 page `SVGGraphicsElement.getBBox()` sat in plain
prose while `StylePropertyMap`, `delete()` and `protocol` beside it were in `<code>`.

**It will not catch that whole class**, and the output says so on every run. Broader shape rules were
measured against all 6,872 shipped notes and rejected: matching CamelCase flags 15.7% of them, because
`macOS`, `JavaScript`, `WebRTC`, `iOS` and `YouTube` are ordinary prose in release notes; matching
hyphenated keywords flags `find-in-page`, `right-to-left` and `drag-and-drop`. These two shapes
together flag 2.7% of the corpus and almost all of those are genuine. A quiet check that is trusted
beats a noisy one that gets skimmed -- single-word names like `StylePropertyMap` still need an eye.

Read-only, and `--check-links` reaches third parties: it requests every host the notes link to, which
across the corpus means 152 of them -- MDN and support.mozilla.org, but also spec drafts, the RFC
editor and Google Play.

Usage:
  note-page.py https://www-dev.springfield.moz.works/en-US/firefox/155.0a1/releasenotes/
  note-page.py /tmp/n155.html                # a page already saved with curl -s ... -o
  note-page.py <src> --markup                # also the raw <p> HTML per note
  note-page.py <src> --audit                 # only the code-formatting audit
  note-page.py <src> --check-links           # resolve the links the authors wrote
  note-page.py --check-url <url> [<url>…]    # resolve links you are about to suggest adding
"""

import argparse
import html as html_mod
import re
import sys
import urllib.error as url_error
import urllib.parse as url_parse
import urllib.request as url_request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

# Headings and notes are matched in one pass so they come out in document order; a note's section is
# whichever heading most recently preceded it.
HEADING_RE = r'<span class="fl-c-release-notes-heading">(.*?)</span>\s*</h\d>'
# `note-[\w-]+`, not `note-\d+`: not every note id is numeric. The 155.0a1 page carries an
# `id="note-mdn"` item in the Developer section, and a digits-only pattern drops it without a word --
# which is what the hand-written snippets this script replaces were doing on every pass.
NOTE_RE = r'<li class="release-note" id="(note-[\w-]+)">(.*?)</li>'
WALK_RE = re.compile(f"{HEADING_RE}|{NOTE_RE}", re.S)

CODE_RE = re.compile(r"<code>(.*?)</code>", re.S)
PARA_RE = re.compile(r"<p>(.*?)</p>", re.S)
BUG_RE = re.compile(r"show_bug\.cgi\?id=(\d+)")
# Two ways a bug reaches a rendered note, and they are not equally trustworthy.
#
# The template renders the note's own bug into `<span class="bug-id">`; that is authoritative. Older
# pages have no such span at all -- on the 99.0.1 page every bug is a link the author typed into the
# prose -- and there the prose link is usually the note's bug, so ignoring it would report no bug for
# a whole dot release. But it is only *usually*: the 99.0 new-contributors note links a bug that is
# not its own, and 795 shipped notes cite bugs in prose.
#
# So both are read and the difference is kept, because prose precedes the span in the list item and a
# search over the whole item silently prefers the untrustworthy one.
BUGSPAN_RE = re.compile(r'<span class="bug-id">.*?</span>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
HREF_RE = re.compile(r'href="([^"]+)"')

# Servers that refuse HEAD but answer GET. Retrying on 403 as well as the two "method" codes is
# deliberate: several documentation hosts front themselves with a CDN that rejects HEAD as 403, and
# reporting that as a broken link would be a false alarm on the most commonly linked host there is.
HEAD_REFUSED = (403, 405, 501)

# Two kinds of redirect are not news, and reporting them buries the kind that is. MDN redirects every
# locale-less URL to `/en-US/...`, which is how notes are supposed to link to it, and a fragment never
# reaches the server at all so it cannot survive a redirect.
#
# This pattern is only ever applied to *one* side of a comparison, to ask whether one path is the other
# with a locale segment added. Stripping it from both sides independently is wrong: two letters is also
# a real first segment -- `support.mozilla.org/kb/...` is the fourth most linked host in the corpus --
# so `/kb/x` would reduce to `/x` while its own redirect target `/en-US/kb/x` reduces to `/kb/x`, and an
# equivalent redirect would be reported as a moved page.
#
# The lookahead accepts end-of-path as well as a following slash, because a bare domain link is a
# real case: `https://blog.mozilla.org/` redirects to `/en/`, whose path is just the locale with
# nothing after it.
LOCALE_RE = re.compile(r"^/[a-z]{2}(?:-[A-Za-z]{2})?(?=/|$)")

# Two shapes, chosen by measurement against the whole shipped corpus -- see the module docstring for
# the ones that were tried and rejected. Deliberately *not* keyed on whether the page formats the same
# token elsewhere: most technical names appear once per page, so the moment a note fails to format one
# it also leaves the reference set, and such a rule is blind to exactly the mistake being sought.
CALL_RE = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\(\)")
DOTTED_RE = re.compile(r"\b[a-z_]\w*(?:\.[a-z_]\w*){2,}\b")
SHAPES = (CALL_RE, DOTTED_RE)

# A dotted identifier and a hostname are the same shape, and hostnames are all over these notes
# (`addons.mozilla.org`, `play.google.com`). Preference names are the thing worth catching, so the
# split is on the final segment: `security.webauth.u2f` stays, `support.mozilla.org` goes.
TLDS = {"com", "org", "net", "io", "dev", "edu", "gov", "co", "uk", "ca", "de", "fr", "jp",
        "info", "me", "tv", "app", "us", "eu", "cn", "ru", "au", "nz", "ch", "nl", "se"}


def text_of(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace collapsed."""
    return html_mod.unescape(re.sub(r"\s+", " ", TAG_RE.sub("", fragment))).strip()


def bug_label(note: dict) -> str:
    """How to describe a note's bug, without overstating where it came from."""
    if not note["bug"]:
        return "no bug link"
    return f"bug {note['bug']}" if note["bug_is_own"] else f"mentions bug {note['bug']}"


def load(src: str) -> str:
    if src.startswith(("http://", "https://")):
        # A page is prose: one bad byte should degrade a character, not abort a review.
        return trainlib.fetch_text(src, errors="replace")
    path = Path(src)
    if not path.is_file():
        sys.exit(f"error: {src} is neither a URL nor a readable file")
    return path.read_text(encoding="utf-8", errors="replace")


def parse(page: str) -> list[dict]:
    """Notes in document order, each tagged with the section heading above it."""
    notes, section = [], "(no section)"
    for m in WALK_RE.finditer(page):
        if m.group(1) is not None:
            heading = text_of(m.group(1))
            if heading:
                section = heading
            continue
        note_id, body = m.group(2), m.group(3)
        para = PARA_RE.search(body)
        # Not every note is a paragraph: the Developer section's `note-mdn` item is a bare link.
        # Falling back to the whole <li> keeps it readable instead of printing an empty line; the
        # bug-id span comes out first so the fallback text does not end in "Bug NNNN".
        markup = (para.group(1) if para
                  else re.sub(r'<span class="bug-id">.*?</span>', "", body, flags=re.S)).strip()
        span = BUGSPAN_RE.search(body)
        bug = BUG_RE.search(span.group(0)) if span else BUG_RE.search(markup)
        notes.append({
            "id": note_id,
            "section": section,
            "bug": bug.group(1) if bug else None,
            # Whether that bug came from the template's field or from a link in the prose.
            "bug_is_own": bool(span),
            "markup": re.sub(r"\s+", " ", markup),
            "text": text_of(markup),
            "code": [text_of(c) for c in CODE_RE.findall(markup)],
            # From the note's own text, so the bug-id link is excluded: those are generated from the
            # bug number and cannot be mistyped, while the links an author wrote can be.
            "links": [html_mod.unescape(u) for u in HREF_RE.findall(markup)],
        })
    return notes


def link_status(url: str, timeout: int = 10) -> tuple:
    """(code, final_url) for a URL, or (None, reason) if it could not be reached.

    HEAD first because a link check does not need the body; GET only where the server refuses.
    Redirects are followed and the final URL returned, because a note pointing at a redirect is
    worth knowing about even though it resolves -- documentation pages get moved.
    """
    for method in ("HEAD", "GET"):
        req = url_request.Request(url, method=method,
                                  headers={"User-Agent": trainlib.USER_AGENT})
        try:
            with url_request.urlopen(req, timeout=timeout) as r:
                return r.status, r.geturl()
        except url_error.HTTPError as e:
            if method == "HEAD" and e.code in HEAD_REFUSED:
                continue
            return e.code, url
        except (url_error.URLError, TimeoutError) as e:
            return None, str(getattr(e, "reason", e))
    return None, "no response"


def same_target(requested: str, final: str) -> bool:
    """Whether a redirect landed somewhere equivalent -- see LOCALE_RE."""
    a, b = url_parse.urlsplit(requested), url_parse.urlsplit(final)
    if a.netloc.lower() != b.netloc.lower() or a.query != b.query:
        return False
    pa, pb = a.path.rstrip("/"), b.path.rstrip("/")
    if pa == pb:
        return True
    return LOCALE_RE.sub("", pa) == pb or LOCALE_RE.sub("", pb) == pa


def check_links(notes: list[dict]) -> None:
    """Resolve every author-written link on the page and report what is wrong with any of them."""
    where = {}
    for n in notes:
        for url in n["links"]:
            where.setdefault(url, []).append(n)
    total = sum(len(n["links"]) for n in notes)
    print(f"  {total} link(s) in {len(notes)} note(s), {len(where)} unique. Bug links are excluded: "
          "they are generated from the bug number.")
    report_links(where)


def check_urls(urls: list[str]) -> None:
    """Resolve URLs handed in directly, for a link you are about to suggest adding.

    The other half of the same job as check_links, and the half that actually caused the prompts this
    script exists to stop: a review pass validating three MDN URLs *before* proposing them cannot use
    the page-walking path, because the links are not on the page yet.
    """
    print(f"  {len(urls)} URL(s) given, {len(dict.fromkeys(urls))} unique")
    report_links({u: [] for u in urls})


def report_links(where: dict) -> None:
    """Resolve each URL once and report the ones worth acting on, with any owning notes."""
    ok = equivalent = moved = broken = 0
    problems = []
    for i, (url, owners) in enumerate(sorted(where.items()), 1):
        print(f"# checking {i}/{len(where)}", file=sys.stderr)
        if not url.startswith(("http://", "https://")):
            broken += 1
            problems.append(("NOT ABSOLUTE", url, url, owners))
            continue
        code, final = link_status(url)
        if code is None:
            broken += 1
            problems.append(("UNREACHABLE", url, final, owners))
        elif code >= 400:
            broken += 1
            problems.append((f"HTTP {code}", url, final, owners))
        elif not same_target(url, final):
            moved += 1
            problems.append(("MOVED", url, final, owners))
        else:
            ok += 1
            if final != url:
                equivalent += 1
    print(f"  {ok} ok ({equivalent} via an equivalent redirect), {moved} moved, {broken} broken")
    for label, url, detail, owners in problems:
        print(f"\n  {label}  {url}")
        if detail != url:
            print(f"      -> {detail}")
        for n in owners:
            bug = bug_label(n)
            print(f"      {n['id']}  {bug}  [{n['section']}]")


def audit(notes: list[dict]) -> list[tuple[dict, list[str], set]]:
    """Notes whose plain prose contains a code-shaped token.

    Returns the note, its hits, and whichever of those hits the page does format somewhere -- that
    subset is the strongest evidence, but it is reported alongside the rest rather than gating it.
    """
    formatted = {c for n in notes for c in n["code"] if c}
    findings = []
    for n in notes:
        # Prose only: a token already inside <code> is correct by definition.
        prose = text_of(CODE_RE.sub(" ", n["markup"]))
        hits = {m for shape in SHAPES for m in shape.findall(prose)}
        hits = {h for h in hits if h.rsplit(".", 1)[-1].lower() not in TLDS}
        # Drop a hit wholly contained in a longer one, so `SVGGraphicsElement.getBBox()` is not
        # also reported as `getBBox`.
        hits = sorted(h for h in hits if not any(h != other and h in other for other in hits))
        if hits:
            findings.append((n, hits, {h for h in hits if h in formatted}))
    return findings


def main() -> None:
    p = argparse.ArgumentParser(
        description="Read a rendered release-notes page: sections, notes, bugs, inline markup.")
    p.add_argument("source", nargs="?",
                   help="release-notes URL, or a path to a saved copy of one. Optional only when "
                        "--check-url is doing the work")
    p.add_argument("--markup", action="store_true", help="also print each note's raw <p> HTML")
    p.add_argument("--audit", action="store_true",
                   help="print only the code-formatting audit")
    p.add_argument("--check-links", action="store_true",
                   help="resolve every author-written link on the page and report broken ones and "
                        "redirects, per note. Fetches through Python rather than curl, so it needs "
                        "no per-host approval -- notes link to 152 different hosts across the corpus")
    p.add_argument("--check-url", metavar="URL", nargs="+", default=None,
                   help="resolve these URLs, for links you are about to suggest adding: on their own "
                        "with no page, or alongside a page's own links. Needs no per-host approval")
    args = p.parse_args()
    if not args.source and not args.check_url:
        p.error("give a release-notes source, or --check-url with the URLs to resolve")
    # Reject rather than ignore, as bug-detail.py does for --full: a flag that silently does nothing
    # is a question the user asked and never got an answer to.
    report_only = args.audit or args.check_links
    if args.markup and (report_only or not args.source):
        p.error("--markup applies to the note listing, which --audit, --check-links and --check-url "
                "replace")

    # A bare --check-url has no page to read, so answer it and stop.
    if args.check_url and not args.source:
        print("LINK CHECK")
        check_urls(args.check_url)
        return

    notes = parse(load(args.source))
    if not notes:
        sys.exit("error: no release notes found on that page -- is it a release-notes URL, and did "
                 "the page markup change? Expected <li class=\"release-note\" id=\"note-...\">.")

    sections = []
    for n in notes:
        if n["section"] not in sections:
            sections.append(n["section"])
    # Notes parsed but no heading matched: say so and carry on. Section labels appear beside every
    # audit and link finding, and the audit's premise is that a note is inconsistent with its section
    # siblings -- both quietly lose their meaning if every note lands in one bucket. Warning rather
    # than exiting because nothing else here depends on sections.
    if len(notes) > 1 and sections == ["(no section)"]:
        print("# WARNING: no section headings matched, so every note below is labelled "
              "(no section) -- the page template may have changed", file=sys.stderr)

    if not report_only:
        print(f"PAGE   {args.source}")
        # The no-bug-link count is here to be subtracted. A page carries items that are not Nucleus
        # notes -- the Developer section's "Developer Information" MDN link is one -- so this total
        # sits one above `relnote-flag.py --coverage`'s note count, and a review reported both
        # numbers side by side with nothing to explain the gap.
        nobug = sum(1 for n in notes if not n["bug"])
        print(f"       {len(notes)} note(s) in {len(sections)} section(s), "
              f"{sum(len(n['code']) for n in notes)} code span(s)"
              + (f", {nobug} with no bug link" if nobug else ""))
        current = None
        for i, n in enumerate(notes, 1):
            if n["section"] != current:
                current = n["section"]
                print(f"\n===== {current}")
            bug = bug_label(n)
            print(f"  [{i}] {n['id']}  {bug}  code:{len(n['code'])}")
            print(f"      {n['text']}")
            if args.markup:
                print(f"      markup: {n['markup']}")
        print()

    if args.audit or not report_only:
        findings = audit(notes)
        print("CODE FORMATTING AUDIT")
        # State the method and its blind spot every time, so a clean result is not read as a style
        # pass.
        print("  Narrow by design: flags only calls like `getBBox()` and dotted names like\n"
              "  `security.webauth.u2f` found in prose. It does NOT see single-word API names\n"
              "  (`StylePropertyMap`), hyphenated keywords (`prefers-reduced-motion`) or lowercase\n"
              "  words (`protocol`) -- rules broad enough to catch those flagged 1 note in 5 across\n"
              "  the shipped corpus, on `macOS` and `JavaScript` and `drag-and-drop`. Read by eye.")
        if findings:
            print(f"  {len(findings)} note(s) to look at:")
            for n, hits, confirmed in findings:
                bug = bug_label(n)
                print(f"\n  {n['id']}  {bug}  [{n['section']}]")
                for h in hits:
                    elsewhere = "  <- this page formats it elsewhere" if h in confirmed else ""
                    print(f"    not code-formatted: {h}{elsewhere}")
                print(f"    {n['text'][:200]}")
        else:
            print("  no code-shaped tokens in prose")

    if args.check_links or args.check_url:
        if args.audit or not report_only:
            print()
        print("LINK CHECK")
        if args.check_links:
            check_links(notes)
        if args.check_url:
            check_urls(args.check_url)


if __name__ == "__main__":
    main()
