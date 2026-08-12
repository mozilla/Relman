#!/usr/bin/env python3
"""Build the shipped-release-notes calibration corpus for the release-note skills.

Every Firefox release note ever published is available as unauthenticated JSON
from Nucleus (the release-note authoring system behind firefox.com/releases):

    https://nucleus.mozilla.org/rna/notes/?format=json      (all notes)
    https://nucleus.mozilla.org/rna/releases/?format=json    (all releases)

A note carries its tag, text, bug number, known-issue and progressive-rollout
flags, and a list of release URLs it is attached to; a release carries product,
channel, version, and release date. Joining the two on the release id yields a
queryable corpus -- no HTML scraping of firefox.com required.

This script does that join, filters to a product/channel/date window, and emits
either the distilled Markdown survey that the skills read as their calibration
reference, or the filtered corpus as JSON.

Two optional passes hit the Bugzilla REST API for context Nucleus does not have:

  --areas       resolve each noted bug to its Bugzilla component, giving the
                distribution of *areas* that produce notes.
  --negative V  for each sampled version, fetch every bug marked fixed in that
                version and contrast it with the handful that earned a note.
                This is the empirical significance bar: the denominator.

Read-only. Network JSON is cached in a work directory (a mktemp dir unless
--workdir is given) so repeat runs and multiple --format passes are cheap.

Usage:
  fetch-shipped-notes.py --format md -o reference/release-notes/shipped-notes-survey.md \\
      --areas --negative 153.0,152.0,151.0,150.0
  fetch-shipped-notes.py --format json -o /tmp/corpus.json
  fetch-shipped-notes.py --format stats
"""

import argparse
import collections
import json
import re
import statistics
import sys
import tempfile
import urllib.parse as url_parse
from pathlib import Path

from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

NUCLEUS_NOTES = "https://nucleus.mozilla.org/rna/notes/?format=json"
NUCLEUS_RELEASES = "https://nucleus.mozilla.org/rna/releases/?format=json"
BUGZILLA_REST = "https://bugzilla.mozilla.org/rest/bug"

USER_AGENT = "Relman-relnotes-survey/1.0 (+release-note skill calibration)"

# How many matches `--search` lists. A broad term matches hundreds ('performance' matches 242), and
# the point of the search is precedent calibration, which a wall of output does not help.
SEARCH_LIST_LIMIT = 40

# Nucleus release URLs look like .../rna/releases/1550/?format=json
RELEASE_ID_RE = re.compile(r"/releases/(\d+)/")
# A major release is X.0 exactly; X.0.Y is a dot release.
MAJOR_VERSION_RE = re.compile(r"^(\d+)\.0$")
# Markdown reference-link definitions Nucleus appends to note bodies.
REF_DEF_RE = re.compile(r"^\s*\[\d+\]:\s*\S+\s*$", re.MULTILINE)
# Inline bug citations, e.g. "([Bug 2047473][1])" or "(see [573369][1])".
BUG_CITE_RE = re.compile(r"\(\s*(?:see\s+)?\[(?:[Bb]ug\s*)?\d+\]\[\d+\]\s*\)")
# Any remaining markdown link: keep the anchor text, drop the target.
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
MD_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[\d+\]")
# Nucleus stores cross-reference stubs ("Reference link to 152.0.4 release notes")
# as notes. They are navigation, not release notes, and would inflate every count.
STUB_RE = re.compile(r"^\s*reference link to\b", re.IGNORECASE)
# Tags that are not hand-authored during the notes pass and should not calibrate
# either the significance bar or the phrasing analysis.
NON_AUTHORED_TAGS = {"Community", "Enterprise"}
# The standing per-release catch-all. It ships in every major, so counting it
# alongside real Fixed notes doubles the apparent Fixed-in-majors rate.
BOILERPLATE_RE = re.compile(r"^(various\s+)?security fix", re.IGNORECASE)


def fetch_json(url: str, cache: Path | None, label: str) -> Any:
    """GET url as JSON, memoized on disk under cache/label.json."""
    if cache is not None:
        blob = cache / f"{label}.json"
        if blob.exists():
            try:
                return json.loads(blob.read_text())
            except ValueError as e:
                # Refetching repairs a half-written cache, so this is not fatal -- but say so, or a
                # write that keeps failing refetches megabytes on every run in silence.
                print(f"# warning: rewriting unreadable cache {blob} ({e})", file=sys.stderr)
    # Nucleus 502s and times out often enough that a single attempt is not evidence it is down;
    # trainlib.fetch_json retries 5xx/429/timeouts, which is why this does not open its own request.
    try:
        payload = trainlib.fetch_json(url)
    except RuntimeError as e:
        sys.exit(f"error: {e}")
    if cache is not None:
        (cache / f"{label}.json").write_text(json.dumps(payload))
    return payload


def release_id(url: str) -> str | None:
    m = RELEASE_ID_RE.search(url)
    return m.group(1) if m else None


def print_notes_for(rel: dict, targets: set, notes: list) -> None:
    """Every public note attached to one release, in Nucleus order."""
    sel = [n for n in notes if n.get("is_public")
           and any(release_id(u) in targets for u in n.get("releases", []))]
    sel.sort(key=lambda n: (n.get("sort_num", 0), n.get("tag") or "", n["id"]))
    print(f"{rel['product']} {rel['version']} ({rel['channel']})  "
          f"released {(rel.get('release_date') or '')[:10]}  "
          f"public={rel.get('is_public')}")
    print(f"{len(sel)} note(s) attached\n")
    for i, n in enumerate(sel, 1):
        bug = f"  bug {n['bug']}" if n.get("bug") else "  (no bug)"
        extra = []
        if n.get("is_known_issue"):
            extra.append("KNOWN ISSUE")
        if n.get("progressive_rollout"):
            extra.append("progressive rollout")
        flags = ("  [" + ", ".join(extra) + "]") if extra else ""
        print(f"{i:>2}. [{n.get('tag') or '(untagged)'}]{bug}{flags}")
        print(f"    {' '.join((n.get('note') or '').split())}")
        print()


def is_major(version: str) -> bool:
    return MAJOR_VERSION_RE.match(version) is not None


def major_number(version: str) -> int:
    """Sort key: 153.0 and 153.0.2 both sort under 153."""
    m = re.match(r"^(\d+)", version)
    return int(m.group(1)) if m else 0


def version_sort_key(version: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))


def clean_note(text: str) -> str:
    """Strip Nucleus markdown scaffolding down to the prose a reader sees."""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = REF_DEF_RE.sub("", t)
    t = BUG_CITE_RE.sub("", t)
    t = MD_LINK_RE.sub(r"\1", t)
    t = MD_REF_LINK_RE.sub(r"\1", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip().rstrip(",").strip()


def first_word(text: str) -> str:
    m = re.match(r"[^A-Za-z0-9]*([A-Za-z0-9'’-]+)", text)
    return m.group(1) if m else ""


def build_pairs(
    notes: list[dict], releases: list[dict], product: str, channel: str, since: str
) -> tuple[list[tuple[dict, dict]], dict[str, dict]]:
    """Join notes to in-scope releases. Returns (release, note) pairs.

    A note can be attached to several releases (known issues especially), so the
    pair list is the right unit for per-release counting; dedupe on note id when
    counting distinct notes.
    """
    scoped = {}
    for r in releases:
        if not r.get("is_public"):
            continue
        if r.get("product") != product or r.get("channel") != channel:
            continue
        if not r.get("release_date") or r["release_date"] < since:
            continue
        rid = release_id(r["url"])
        if rid:
            scoped[rid] = r

    pairs = []
    stubs = 0
    for n in notes:
        if not n.get("is_public"):
            continue
        if STUB_RE.match(n.get("note", "")):
            stubs += 1
            continue
        for u in n.get("releases", []):
            rid = release_id(u)
            if rid in scoped:
                pairs.append((scoped[rid], n))
    return pairs, scoped, stubs


def fetch_bug_fields(bug_ids: list[int], fields: str, cache: Path | None, label: str) -> dict[int, dict]:
    """Batch-fetch Bugzilla bugs by id. Security-restricted bugs simply don't come back."""
    out: dict[int, dict] = {}
    batch_size = 120
    batches = [bug_ids[i : i + batch_size] for i in range(0, len(bug_ids), batch_size)]
    for i, batch in enumerate(batches):
        qs = url_parse.urlencode({"id": ",".join(str(b) for b in batch), "include_fields": fields})
        payload = fetch_json(f"{BUGZILLA_REST}?{qs}", cache, f"{label}-{i}")
        for bug in payload.get("bugs", []):
            out[bug["id"]] = bug
    return out


def fetch_fixed_in_version(version: str, cache: Path | None) -> list[dict]:
    """Every bug flagged fixed in a given Firefox major version."""
    major = major_number(version)
    qs = url_parse.urlencode(
        {
            f"cf_status_firefox{major}": "fixed",
            "include_fields": "id,component,product",
            "limit": "0",
        }
    )
    payload = fetch_json(f"{BUGZILLA_REST}?{qs}", cache, f"fixed-{major}")
    return payload.get("bugs", [])


def summarize(pairs: list[tuple[dict, dict]], scoped: dict[str, dict]) -> dict:
    """Compute every statistic the survey reports."""
    notes_by_id = {n["id"]: n for _, n in pairs}
    per_version: dict[str, list[dict]] = collections.defaultdict(list)
    for r, n in pairs:
        per_version[r["version"]].append(n)

    majors = {v: ns for v, ns in per_version.items() if is_major(v)}
    dots = {v: ns for v, ns in per_version.items() if not is_major(v)}
    major_counts = sorted(((v, len(ns)) for v, ns in majors.items()), key=lambda x: version_sort_key(x[0]))
    counts_only = [c for _, c in major_counts]

    def tag_of(n: dict) -> str:
        return n.get("tag") or "(untagged)"

    by_tag: dict[str, list[dict]] = collections.defaultdict(list)
    for n in notes_by_id.values():
        by_tag[tag_of(n)].append(n)

    major_note_ids = {n["id"] for ns in majors.values() for n in ns}
    dot_note_ids = {n["id"] for ns in dots.values() for n in ns}

    # The Fixed notes that already cleared the bar for a *major* release. As the
    # release cycle shortens, Fixed is likely to be used more in majors, so these
    # are the calibration set for where that threshold currently sits.
    fixed_in_majors = []
    substantive_per_major: dict[str, int] = {v: 0 for v in majors}
    boilerplate_fixed = 0
    for version, ns in sorted(majors.items(), key=lambda x: version_sort_key(x[0])):
        for n in ns:
            if (n.get("tag") or "") != "Fixed":
                continue
            fixed_in_majors.append((version, n))
            if BOILERPLATE_RE.match(clean_note(n["note"])):
                boilerplate_fixed += 1
            else:
                substantive_per_major[version] += 1

    # Is the Fixed-in-majors bar moving? Compare the older and newer halves of
    # the window, plus the most recent few releases.
    ordered_majors = sorted(majors, key=version_sort_key)

    def rate(versions: list[str]) -> float:
        return (
            sum(substantive_per_major[v] for v in versions) / len(versions) if versions else 0.0
        )

    mid = len(ordered_majors) // 2
    older, newer = ordered_majors[:mid], ordered_majors[mid:]
    recent = ordered_majors[-7:]
    fixed_trend = {
        "substantive_per_major": [(v, substantive_per_major[v]) for v in ordered_majors],
        "boilerplate": boilerplate_fixed,
        "substantive_total": sum(substantive_per_major.values()),
        "older_span": (older[0], older[-1]) if older else ("", ""),
        "older_rate": rate(older),
        "newer_span": (newer[0], newer[-1]) if newer else ("", ""),
        "newer_rate": rate(newer),
        "recent_span": (recent[0], recent[-1]) if recent else ("", ""),
        "recent_rate": rate(recent),
        "zero_releases": sum(1 for v in ordered_majors if substantive_per_major[v] == 0),
    }

    tag_stats = {}
    for tag, ns in by_tag.items():
        cleaned = [clean_note(n["note"]) for n in ns]
        cleaned = [c for c in cleaned if c]
        words = [len(c.split()) for c in cleaned]
        tag_stats[tag] = {
            "count": len(ns),
            "in_majors": sum(1 for n in ns if n["id"] in major_note_ids),
            "in_dots": sum(1 for n in ns if n["id"] in dot_note_ids),
            "with_bug": sum(1 for n in ns if n.get("bug")),
            "median_words": int(statistics.median(words)) if words else 0,
            "max_words": max(words) if words else 0,
            "openers": collections.Counter(first_word(c) for c in cleaned).most_common(12),
            "examples": cleaned,
            "notes": ns,
        }

    # Median length across hand-authored notes only: the Community blurb and the
    # Enterprise boilerplate are generated elsewhere and would skew it badly.
    authored_words = [
        len(clean_note(n["note"]).split())
        for n in notes_by_id.values()
        if (n.get("tag") or "") not in NON_AUTHORED_TAGS and clean_note(n["note"])
    ]

    return {
        "pairs": len(pairs),
        "distinct_notes": len(notes_by_id),
        "releases": len(scoped),
        "major_notes": len(major_note_ids),
        "dot_note_count": len(dot_note_ids),
        "fixed_in_majors": fixed_in_majors,
        "fixed_trend": fixed_trend,
        "authored_median_words": int(statistics.median(authored_words)) if authored_words else 0,
        "majors": major_counts,
        "major_total": sum(counts_only),
        "major_mean": statistics.mean(counts_only) if counts_only else 0,
        "major_median": statistics.median(counts_only) if counts_only else 0,
        "major_min": min(counts_only) if counts_only else 0,
        "major_max": max(counts_only) if counts_only else 0,
        "dot_releases": len(dots),
        "dot_notes": len(dot_note_ids),
        "dot_with_bug": sum(1 for i in dot_note_ids if notes_by_id[i].get("bug")),
        "major_with_bug": sum(1 for i in major_note_ids if notes_by_id[i].get("bug")),
        "known_issues": sum(1 for n in notes_by_id.values() if n.get("is_known_issue")),
        "rollout": sum(1 for n in notes_by_id.values() if n.get("progressive_rollout")),
        "tag_stats": tag_stats,
        "notes_by_id": notes_by_id,
        "per_version": per_version,
    }


def pick_examples(cleaned: list[str], k: int = 5) -> list[str]:
    """A spread of examples: shortest, longest, and evenly-spaced middles."""
    if not cleaned:
        return []
    ordered = sorted(set(cleaned), key=lambda c: len(c.split()))
    if len(ordered) <= k:
        return ordered
    idx = [round(i * (len(ordered) - 1) / (k - 1)) for i in range(k)]
    return [ordered[i] for i in sorted(set(idx))]


def emit_markdown(s: dict, meta: dict, areas: dict | None, negative: dict | None) -> str:
    o: list[str] = []
    w = o.append

    w("# What actually gets a release note")
    w("")
    w(
        f"Empirical calibration for the release-note skills: every **{meta['product']} / "
        f"{meta['channel']}** note published since **{meta['since'][:10]}**, pulled from Nucleus "
        f"and joined to its release."
    )
    w("")
    w(
        "Regenerate with `scripts/relnotes/fetch-shipped-notes.py --format md "
        f"-o {meta['outpath']} --areas --negative {','.join(negative['versions']) if negative else '153.0,152.0'}`. "
        "Counts below are from the run recorded at the bottom of this file; refresh after each cycle."
    )
    w("")
    w("## The bar, in one line")
    w("")
    if negative:
        rate = negative["overall_rate"]
        w(
            f"**A major release ships {s['major_median']:.0f} notes (median; range "
            f"{s['major_min']}–{s['major_max']}) drawn from ~{negative['mean_fixed']:,.0f} bugs "
            f"fixed per cycle — about {rate:.1f}%, or 1 note per {round(100 / rate) if rate else 0} "
            f"fixed bugs.**"
        )
    else:
        w(
            f"**A major release ships {s['major_median']:.0f} notes (median; range "
            f"{s['major_min']}–{s['major_max']}).**"
        )
    w("")
    w(
        "**This is a publication rate, not a shortlist budget — do not use it to prune.** It is "
        "what survived *after* a developer was asked and Release Management made a call. Discovery "
        "sits upstream of both: its job is to surface bugs worth asking a developer about, and the "
        "cost of the two errors is wildly asymmetric. A surplus candidate costs one question in a "
        "bug; a missed one ships a release with a note nobody wrote. So **err toward including** "
        "and let the tiering carry the uncertainty."
    )
    w("")
    w(
        "What the number *is* good for: sanity-checking the final published set, and calibrating "
        "the *shape* of a note-worthy change (below) so the asking is well-targeted rather than "
        "indiscriminate."
    )
    w("")

    w("## Notes per major release")
    w("")
    w("| Version | Notes | | Version | Notes |")
    w("|---|---:|---|---|---:|")
    majors = s["majors"]
    half = (len(majors) + 1) // 2
    left, right = majors[:half], majors[half:]
    for i in range(half):
        lv, lc = left[i]
        if i < len(right):
            rv, rc = right[i]
            w(f"| {lv} | {lc} | | {rv} | {rc} |")
        else:
            w(f"| {lv} | {lc} | | | |")
    w("")
    w(
        f"{len(majors)} major releases, {s['major_total']} notes, mean {s['major_mean']:.1f}. "
        f"Plus {s['dot_releases']} dot releases carrying {s['dot_notes']} notes."
    )
    w("")

    w("## Tags, and what each is really used for")
    w("")
    w("| Tag | Notes | In majors | In dots | Has bug | Median words |")
    w("|---|---:|---:|---:|---:|---:|")
    for tag, st in sorted(s["tag_stats"].items(), key=lambda x: -x[1]["count"]):
        w(
            f"| {tag} | {st['count']} | {st['in_majors']} | {st['in_dots']} | "
            f"{st['with_bug']} | {st['median_words']} |"
        )
    w("")
    w(
        f"Notes are short — median **{s['authored_median_words']} words** across hand-authored "
        "notes (excluding the generated Community and Enterprise boilerplate). A candidate that "
        "needs three sentences to explain is usually either two notes or not a note."
    )
    w("")
    w(
        "**The Fixed/major split is the number to watch.** Of "
        f"{s['tag_stats'].get('Fixed', {}).get('count', 0)} `Fixed` notes, "
        f"{s['tag_stats'].get('Fixed', {}).get('in_dots', 0)} shipped in dot releases against "
        f"{s['tag_stats'].get('Fixed', {}).get('in_majors', 0)} in majors — and "
        f"{s['fixed_trend']['boilerplate']} of those majors are just the standing security catch-all. "
        "Majors are carried by `New`, `HTML5`/`Developer`, and `Changed`; dot releases are where "
        "fixes live. Treat that as a description of past practice rather than a rule — it is "
        "already changing, and the next section quantifies how fast and characterizes the threshold."
    )
    w("")

    w("### Opening words by tag")
    w("")
    w(
        "The style guide's tense rule shows up in the data — use this to check a draft's "
        "register against what actually ships."
    )
    w("")
    for tag, st in sorted(s["tag_stats"].items(), key=lambda x: -x[1]["count"]):
        if st["count"] < 10:
            continue
        openers = ", ".join(f"`{word}` ({n})" for word, n in st["openers"][:8] if word)
        w(f"- **{tag}** — {openers}")
    w("")

    w("### Representative notes")
    w("")
    w("Real shipped text, spread from shortest to longest within each tag.")
    w("")
    for tag, st in sorted(s["tag_stats"].items(), key=lambda x: -x[1]["count"]):
        if st["count"] < 5:
            continue
        w(f"**{tag}**")
        w("")
        for ex in pick_examples(st["examples"], 5):
            flat = " ".join(ex.split())
            if len(flat) > 300:
                flat = flat[:297] + "..."
            w(f"- {flat}")
        w("")

    t = s["fixed_trend"]
    w("## Where the `Fixed` threshold currently sits (majors)")
    w("")
    w(
        f"{len(s['fixed_in_majors'])} `Fixed` notes shipped in a major release in this window — but "
        f"**{t['boilerplate']} of those are the standing `Various security fixes.` catch-all**, one "
        f"per release. The real count of substantive mainline fix notes is **{t['substantive_total']}** "
        f"across {len(s['majors'])} releases."
    )
    w("")
    w("**And that bar is already moving:**")
    w("")
    w(
        f"| Window | Substantive `Fixed` notes per major |\n|---|---:|\n"
        f"| {t['older_span'][0]}–{t['older_span'][1]} | {t['older_rate']:.2f} |\n"
        f"| {t['newer_span'][0]}–{t['newer_span'][1]} | {t['newer_rate']:.2f} |\n"
        f"| {t['recent_span'][0]}–{t['recent_span'][1]} (most recent) | {t['recent_rate']:.2f} |"
    )
    w("")
    w(
        f"Per release: {', '.join(f'{v} ({c})' for v, c in t['substantive_per_major'])}. "
        f"{t['zero_releases']} of {len(s['majors'])} majors carried no substantive fix note at all — "
        "and nearly all of those are in the older half of the window."
    )
    w("")
    w(
        f"So the practice has already shifted by roughly "
        f"{t['recent_rate'] / t['older_rate']:.0f}× without anyone changing the guidance. Any "
        "deliberate decision to lean on `Fixed` for majors as the cycle shortens is an "
        "acceleration of a trend in progress, not a new departure — which makes the threshold "
        "question the practical one. The full list below is the evidence base."
    )
    w("")
    for version, n in s["fixed_in_majors"]:
        cleaned = clean_note(n["note"])
        if BOILERPLATE_RE.match(cleaned):
            continue  # the catch-all, counted above; listing it 26 times adds nothing
        flat = " ".join(cleaned.split())
        if len(flat) > 220:
            flat = flat[:217] + "..."
        bug = f" (bug {n['bug']})" if n.get("bug") else ""
        w(f"- **{version}** — {flat}{bug}")
    w("")
    w(
        "Read across them and a usable threshold falls out. The fixes that clear the bar are ones "
        "where **an everyday interaction was reliably broken for an identifiable group of users, "
        "and they could tell**. Concretely, the recurring shapes are:"
    )
    w("")
    w(
        "- **Operating-system integration papercuts** — clipboard and drag-and-drop (image "
        "transparency lost on copy, dragging an image to another application inserting a URL "
        "instead), multi-monitor behavior (windows reopening on the wrong display, wrong "
        "resolution reported to sites), fractional display scaling."
    )
    w(
        "- **Text input and navigation** — arrow-key movement going the wrong way in "
        "right-to-left text, Paste going missing from context menus on major sites."
    )
    w(
        "- **Breakage with a clear population** — a language pack silently disabling after a major "
        "update, an input method crashing, emoji vanishing under Lockdown mode."
    )
    w(
        "- **Protocol or performance work with a describable user effect** — HTTP/3 upload "
        "robustness on unstable networks, requests timing out on non-conforming headers. Note the "
        "contrast: these earn notes because the user-visible consequence is stateable, not because "
        "a benchmark moved."
    )
    w("")
    w(
        "What is **absent** is as informative: no performance micro-optimizations, no cosmetic or "
        "spacing corrections, no edge-case correctness fixes, and nothing whose description would "
        "require naming an internal component. Several notes are platform-scoped (macOS, Windows, "
        "Linux/GNOME) — narrow platform reach is not disqualifying when the breakage is severe."
    )
    w("")

    if areas:
        w("## Which areas produce notes")
        w("")
        w(
            f"Bugzilla component of every noted bug ({areas['resolved']} of {areas['requested']} "
            "resolved; the rest are security-restricted or no longer readable)."
        )
        w("")
        w("| Component | Notes |")
        w("|---|---:|")
        for comp, n in areas["top"]:
            w(f"| {comp} | {n} |")
        w("")
        w(
            f"Long tail: {areas['distinct']} distinct components for {areas['resolved']} notes. "
            "Notes cluster in the front end and in web-platform components, and are almost absent "
            "from build, test, and internal-infrastructure components — which is what the "
            "mechanical-noise filter in the scan encodes."
        )
        w("")

    if negative:
        w("## The denominator (what does *not* get a note)")
        w("")
        w(
            "For each sampled version, every bug marked `fixed` for that version versus the notes "
            "that shipped with it."
        )
        w("")
        w("| Version | Bugs fixed | Notes | Rate |")
        w("|---|---:|---:|---:|")
        for v in negative["versions"]:
            row = negative["per_version"][v]
            w(f"| {v} | {row['fixed']:,} | {row['notes']} | {row['rate']:.2f}% |")
        w("")
        w(
            f"Aggregate: **{negative['total_notes']} notes out of {negative['total_fixed']:,} fixed "
            f"bugs = {negative['overall_rate']:.2f}%.**"
        )
        w("")
        if negative.get("component_conversion"):
            w(
                f"Highest note yield among components with at least {negative['min_volume']} "
                "fixed bugs in the sampled versions (below that, one note reads as a huge "
                "percentage and means nothing):"
            )
            w("")
            w("| Component | Fixed | Noted | Rate |")
            w("|---|---:|---:|---:|")
            for comp, fixed, noted, rate in negative["component_conversion"]:
                w(f"| {comp} | {fixed:,} | {noted} | {rate:.1f}% |")
            w("")
        if negative.get("component_zero_yield"):
            w(
                "And the opposite end — the busiest components that produced **no** notes at all "
                "across four releases. This is where an unfiltered scan burns most of its effort:"
            )
            w("")
            w("| Component | Fixed | Noted |")
            w("|---|---:|---:|")
            for comp, fixed, _noted, _rate in negative["component_zero_yield"]:
                w(f"| {comp} | {fixed:,} | 0 |")
            w("")
            w(
                f"**Read this table with one caveat.** The scope here is {meta['product']} / "
                f"{meta['channel']}, so components belonging to a *different* product — the "
                "`Firefox for Android ::` entries above especially — are structurally zero: their "
                "bugs are flagged fixed in the same Gecko version but their notes ship in a "
                "different product's notes entirely. That is a scoping artifact, **not** evidence "
                "that mobile work is unnotable. The genuinely informative rows are the ones in "
                "products that *do* feed these notes: test suites, build and lint infrastructure, "
                "and engine internals (JIT, WebAssembly, WebRender, SVG) carry heavy fix volume "
                "and reliably produce nothing. Re-run with `--product 'Firefox for Android'` to "
                "calibrate mobile separately."
            )
            w("")

    w("## Observations that bear on discovery")
    w("")
    w(
        f"- **Bug numbers are recorded on {s['major_with_bug']} of {s['major_notes']} major-release "
        f"notes ({100 * s['major_with_bug'] / s['major_notes']:.0f}%) versus {s['dot_with_bug']} of "
        f"{s['dot_note_count']} dot-release notes "
        f"({100 * s['dot_with_bug'] / s['dot_note_count']:.0f}%).** Dot releases require bug links "
        "and mainline notes don't, which is exactly the gap you see. Nucleus keeps the bug number "
        "as a field even when the published note doesn't render a link — so this corpus can be "
        "joined to Bugzilla either way."
    )
    w(
        f"- **{s['rollout']} notes are marked as progressive rollouts.** A gated or staged feature "
        "does get noted; being behind a rollout is not by itself a reason to hold a note, but it "
        "changes the wording."
    )
    w(
        f"- **{s['known_issues']} known-issue notes.** These recur across several versions, which is "
        "why per-release counting has to use note–release pairs rather than distinct notes."
    )
    w(
        "- **The `HTML5` tag is still in live use** alongside `Developer`, so web-platform notes "
        "appear under more than one tag historically. Match whatever the target product is already "
        "using rather than normalizing."
    )
    w("")

    w("## Provenance")
    w("")
    w(f"- Source: `{NUCLEUS_NOTES}` and `{NUCLEUS_RELEASES}` (public, unauthenticated).")
    w(
        f"- Scope: product `{meta['product']}`, channel `{meta['channel']}`, released on or after "
        f"`{meta['since'][:10]}`."
    )
    w(
        f"- {s['releases']} releases in scope, {s['distinct_notes']} distinct public notes, "
        f"{s['pairs']} note–release pairs."
    )
    if areas or negative:
        w(f"- Bugzilla REST used for {'component lookup' if areas else ''}"
          f"{' and ' if areas and negative else ''}{'fixed-bug denominators' if negative else ''}.")
    w(f"- Generated by `scripts/relnotes/fetch-shipped-notes.py`; counts are as of the run date.")
    return "\n".join(o) + "\n"


def emit_stats(s: dict) -> str:
    o = [
        f"releases in scope : {s['releases']}",
        f"note-release pairs: {s['pairs']}",
        f"distinct notes    : {s['distinct_notes']}",
        f"majors            : {len(s['majors'])} ({s['major_total']} notes, "
        f"mean {s['major_mean']:.1f}, median {s['major_median']:.0f}, "
        f"range {s['major_min']}-{s['major_max']})",
        f"dot releases      : {s['dot_releases']} ({s['dot_notes']} notes)",
        f"known issues      : {s['known_issues']}",
        f"progressive roll  : {s['rollout']}",
        "",
        "tags:",
    ]
    for tag, st in sorted(s["tag_stats"].items(), key=lambda x: -x[1]["count"]):
        o.append(f"  {tag:<12} {st['count']:>4}  (majors {st['in_majors']:>3}, "
                 f"dots {st['in_dots']:>3}, with bug {st['with_bug']:>3}, "
                 f"median {st['median_words']:>2}w)")
    o.append("")
    o.append("notes per major:")
    for v, c in s["majors"]:
        o.append(f"  {v:<8} {c}")
    return "\n".join(o) + "\n"


def emit_json(s: dict, meta: dict) -> str:
    records = []
    for r, n in meta["pairs"]:
        records.append(
            {
                "note_id": n["id"],
                "version": r["version"],
                "release_date": r["release_date"],
                "is_major": is_major(r["version"]),
                "tag": n.get("tag") or "",
                "bug": n.get("bug"),
                "is_known_issue": n.get("is_known_issue", False),
                "progressive_rollout": n.get("progressive_rollout", False),
                "note_raw": n["note"],
                "note_clean": clean_note(n["note"]),
            }
        )
    records.sort(key=lambda x: (version_sort_key(x["version"]), x["tag"], x["note_id"]))
    return json.dumps(
        {
            "scope": {
                "product": meta["product"],
                "channel": meta["channel"],
                "since": meta["since"],
                "source": [NUCLEUS_NOTES, NUCLEUS_RELEASES],
            },
            "counts": {
                "releases": s["releases"],
                "pairs": s["pairs"],
                "distinct_notes": s["distinct_notes"],
            },
            "notes": records,
        },
        indent=2,
    )


def compute_areas(s: dict, cache: Path | None) -> dict:
    bug_ids = sorted({n["bug"] for n in s["notes_by_id"].values() if n.get("bug")})
    bugs = fetch_bug_fields(bug_ids, "id,component,product,summary", cache, "noted-bugs")
    counter = collections.Counter(
        f"{b['product']} :: {b['component']}" for b in bugs.values()
    )
    return {
        "requested": len(bug_ids),
        "resolved": len(bugs),
        "distinct": len(counter),
        "top": counter.most_common(25),
        "by_bug": bugs,
    }


def compute_negative(s: dict, versions: list[str], areas: dict | None, cache: Path | None) -> dict:
    per_version = {}
    total_fixed = total_notes = 0
    fixed_components: collections.Counter = collections.Counter()
    for v in versions:
        fixed = fetch_fixed_in_version(v, cache)
        notes = s["per_version"].get(v, [])
        rate = 100.0 * len(notes) / len(fixed) if fixed else 0.0
        per_version[v] = {"fixed": len(fixed), "notes": len(notes), "rate": rate}
        total_fixed += len(fixed)
        total_notes += len(notes)
        for b in fixed:
            fixed_components[f"{b['product']} :: {b['component']}"] += 1

    component_conversion: list = []
    component_zero_yield: list = []
    if areas:
        noted_components = collections.Counter()
        sampled_bugs = {
            n["bug"] for v in versions for n in s["per_version"].get(v, []) if n.get("bug")
        }
        for bug_id in sampled_bugs:
            b = areas["by_bug"].get(bug_id)
            if b:
                noted_components[f"{b['product']} :: {b['component']}"] += 1
        # A component with 3 fixed bugs and 1 note is not a 33% converter, it is
        # noise. Require real volume before ranking by rate.
        min_volume = 25
        rows = []
        for comp, fixed in fixed_components.items():
            if fixed < min_volume:
                continue
            noted = noted_components.get(comp, 0)
            rows.append((comp, fixed, noted, 100.0 * noted / fixed if fixed else 0.0))
        rows.sort(key=lambda r: (-r[3], -r[1]))
        component_conversion = [r for r in rows if r[2] > 0][:20]
        # Where a scan burns effort for nothing: high fix volume, zero notes.
        component_zero_yield = sorted(
            (r for r in rows if r[2] == 0), key=lambda r: -r[1]
        )[:20]

    return {
        "versions": versions,
        "per_version": per_version,
        "total_fixed": total_fixed,
        "total_notes": total_notes,
        "overall_rate": 100.0 * total_notes / total_fixed if total_fixed else 0.0,
        "mean_fixed": total_fixed / len(versions) if versions else 0,
        "component_conversion": component_conversion,
        "component_zero_yield": component_zero_yield,
        "min_volume": 25,
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Build the shipped-release-notes calibration corpus from Nucleus.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--product", default="Firefox", help="Nucleus product (default: Firefox)")
    p.add_argument("--channel", default="Release", help="Nucleus channel (default: Release)")
    p.add_argument(
        "--since",
        default=None,
        help="only releases on or after this ISO date (default: 24 months back from --months)",
    )
    p.add_argument("--months", type=int, default=24, help="window in months if --since is absent")
    p.add_argument(
        "--workdir",
        default=None,
        help="cache directory for fetched JSON (default: a fresh mktemp dir, left in place)",
    )
    p.add_argument("--areas", action="store_true", help="resolve noted bugs to Bugzilla components")
    p.add_argument(
        "--negative",
        default=None,
        help="comma-separated major versions to compute fixed-bug denominators for, e.g. 153.0,152.0",
    )
    p.add_argument("--notes-for", default=None,
                   help="print every note attached to this version (e.g. 155.0a1) for the given "
                        "--product/--channel, in Nucleus order. Accepts a comma list "
                        "(153.0.1,153.0.2,153.0.3) so a dot-release series needs one call, not a "
                        "shell loop. This is the input to a review pass; use it instead of fetching "
                        "and filtering Nucleus by hand.")
    p.add_argument("--search", default=None,
                   help="case-insensitive regex: show every shipped note matching it, across ALL "
                        "channels and years. Use this to check precedent before proposing a "
                        "candidate -- 'have we ever noted this kind of thing?'")
    p.add_argument("--format", choices=["md", "json", "stats"], default="stats")
    p.add_argument("-o", "--output", default=None, help="write to this path instead of stdout")
    args = p.parse_args()

    if args.since:
        since = args.since if "T" in args.since else f"{args.since}T00:00:00Z"
    else:
        # Approximate months back without pulling in dateutil.
        import datetime

        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=int(args.months * 30.44)
        )
        since = cutoff.strftime("%Y-%m-01T00:00:00Z")

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="relnotes-"))
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"# cache: {workdir}", file=sys.stderr)

    notes = fetch_json(NUCLEUS_NOTES, workdir, "nucleus-notes")
    releases = fetch_json(NUCLEUS_RELEASES, workdir, "nucleus-releases")

    if args.notes_for:
        rel_by_id = {release_id(r["url"]): r for r in releases}
        # A comma list, so comparing a dot-release series does not need a shell loop around this
        # script -- `--notes-for 153.0.1,153.0.2,153.0.3` is one invocation.
        wanted = [v.strip() for v in args.notes_for.replace(",", " ").split() if v.strip()]
        for pos, version in enumerate(wanted):
            targets = {
                i for i, r in rel_by_id.items()
                if r.get("product") == args.product and r.get("channel") == args.channel
                and r.get("version") == version
            }
            if not targets:
                # Sorted as versions, not as strings: lexically "99.0" beats "153.0", so a plain
                # sort offered 98.0 and 99.0 as the most recent Firefox releases. Nucleus also
                # carries non-numeric versions ('duplicate-26.0'), hence the tolerant key.
                def vkey(v: str):
                    parts = re.findall(r"\d+", v)
                    return ([int(x) for x in parts] if parts else [-1], v)
                avail = sorted({r["version"] for r in rel_by_id.values()
                                if r.get("product") == args.product
                                and r.get("channel") == args.channel}, key=vkey)[-6:]
                if avail:
                    sys.exit(f"error: no {args.product} {args.channel} release {version!r}. "
                             f"Recent: {', '.join(avail)}")
                # No releases at all for that product/channel means the *product* is the mistake, and
                # filtering the hint by it leaves nothing -- so name what does exist instead.
                products = sorted({r.get("product") or "?" for r in rel_by_id.values()})
                channels = sorted({r.get("channel") or "?" for r in rel_by_id.values()
                                   if r.get("product") == args.product})
                sys.exit(f"error: no releases at all for --product {args.product!r} "
                         f"--channel {args.channel!r}.\n"
                         f"       products in Nucleus: {', '.join(products)}\n"
                         + (f"       channels for {args.product!r}: {', '.join(channels)}"
                            if channels else
                            "       (that product name matches nothing; note the full form, "
                            "e.g. 'Firefox for Android' rather than 'Android')"))
            if pos:
                print()
            print_notes_for(rel_by_id[sorted(targets)[0]], targets, notes)
        return

    if args.search:
        rx = re.compile(args.search, re.IGNORECASE)
        rel_by_id = {re.search(r"/releases/(\d+)/", r["url"]).group(1): r for r in releases}
        hits = []
        for n in notes:
            if not n.get("is_public") or not rx.search(n.get("note") or ""):
                continue
            vers = sorted({
                f"{rel_by_id[i]['product'].replace('Firefox for ', '')} "
                f"{rel_by_id[i]['version']}/{rel_by_id[i]['channel'][:3]}"
                for u in n.get("releases", [])
                if (i := release_id(u)) in rel_by_id
            })
            hits.append((vers, n))
        print(f"{len(hits)} shipped note(s) match {args.search!r} across "
              f"{len(notes)} notes (all products, channels, years)")
        if not hits:
            print("  NO PRECEDENT -- this kind of change has never been noted.")
        # The header above carries the real total, but the listing is what gets read and precedent
        # counts get quoted from it, so a capped list has to say it is capped.
        if len(hits) > SEARCH_LIST_LIMIT:
            print(f"  showing the first {SEARCH_LIST_LIMIT}; narrow the search to see the rest")
        for vers, n in hits[:SEARCH_LIST_LIMIT]:
            where = ", ".join(vers[:3]) or "(unattached)"
            # The bug number is the point of a precedent search: a carry-forward candidate needs to
            # cite the earlier note's bug, and looking it up by hand afterwards is the whole cost
            # this search exists to avoid. Nucleus carries it, so print it.
            bug = f"bug {n['bug']}" if n.get("bug") else "no bug recorded"
            print(f"  [{where}] ({n.get('tag') or '-'}) [{bug}] "
                  f"{' '.join(clean_note(n['note']).split())[:150]}")
        return
    print(f"# fetched {len(notes)} notes, {len(releases)} releases", file=sys.stderr)

    pairs, scoped, stubs = build_pairs(notes, releases, args.product, args.channel, since)
    print(f"# skipped {stubs} cross-reference stubs", file=sys.stderr)
    if not pairs:
        sys.exit(
            f"error: no notes matched product={args.product!r} channel={args.channel!r} "
            f"since={since!r}. Check --product/--channel spelling against the Nucleus data."
        )
    s = summarize(pairs, scoped)

    areas = compute_areas(s, workdir) if args.areas else None
    negative = None
    if args.negative:
        versions = [v.strip() for v in args.negative.split(",") if v.strip()]
        missing = [v for v in versions if v not in s["per_version"]]
        if missing:
            print(f"# warning: no notes in scope for {', '.join(missing)}", file=sys.stderr)
        negative = compute_negative(s, versions, areas, workdir)

    meta = {
        "product": args.product,
        "channel": args.channel,
        "since": since,
        "pairs": pairs,
        "outpath": args.output or "reference/release-notes/shipped-notes-survey.md",
    }

    if args.format == "md":
        out = emit_markdown(s, meta, areas, negative)
    elif args.format == "json":
        out = emit_json(s, meta)
    else:
        out = emit_stats(s)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(out)
        print(f"# wrote {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)


if __name__ == "__main__":
    main()
