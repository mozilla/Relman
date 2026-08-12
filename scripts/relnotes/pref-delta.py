#!/usr/bin/env python3
"""Find preference flips in a window and resolve each preference's real default per channel.

Two jobs, both of which the release-note discovery pass gets wrong by hand:

1. **Flip detection.** A feature's code often lands months before it becomes live; the commit that
   makes it noteworthy is a one-line default change with an unremarkable subject. This script finds
   those by comparing the *endpoints* of the window rather than replaying commits -- land/backout/
   re-land churn inside one window makes per-commit replay actively wrong. The comparison is between
   fully *parsed* preference tables, not between lines of a git diff: reading a diff cannot reliably
   tell you which preference a changed `value:` line belongs to, and getting that wrong drops
   changes silently (see `changed_prefs`).

2. **Effective default per channel.** `StaticPrefList.yaml` sets 131 defaults through `@DEFINE@`
   indirection (`value: @IS_NIGHTLY_BUILD@`) and wraps whole entries in `#ifdef` blocks, so the
   literal on the `value:` line is frequently not the answer. This script runs a small C-style
   preprocessor over the file once per (channel, platform) and reports what each channel actually
   gets.

Everything is read from `origin/main` via `git show`, never the working tree -- the working tree is
routinely on another branch and reports pre-landing defaults.

Usage:
  pref-delta.py --range <build-commit>..<build-commit>     # boundaries, never dates
  pref-delta.py --range FIREFOX_152_0_RELEASE..FIREFOX_153_0_RELEASE
  pref-delta.py --lookup browser.nova.enabled,browser.smartwindow.enabled
  pref-delta.py --range A..B --format json
"""

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trainlib  # noqa: E402

STATIC_PREF_LIST = "modules/libpref/init/StaticPrefList.yaml"
# The other half of the shared base. StaticPrefList holds prefs with C++ mirrors; everything else
# with a default lives here -- 1,968 `pref()` declarations, including whole families like
# `captivedetect.*`. Omitting it made `--lookup captivedetect.canonicalURL` answer
# "NOT FOUND at origin/main" for a preference three lines long in the tree, and made every flip in
# this file invisible to a scan that calls FLIPPED ON the strongest signal there is. It is not rare
# churn either: 7, 17, 5 and 31 commits touched it in the 151-154 cycles.
ALL_JS = "modules/libpref/init/all.js"
FIREFOX_JS = "browser/app/profile/firefox.js"
# Android's counterpart to firefox.js. Ships with GeckoView/Fenix, so it is where a preference is
# turned on for Android alone, with nothing changing in any file a desktop-only scan reads.
GECKOVIEW_JS = "mobile/android/app/geckoview-prefs.js"
PREF_FILES = [STATIC_PREF_LIST, ALL_JS, FIREFOX_JS, GECKOVIEW_JS]

# firefox.js: pref("name", value);  (also user_pref/sticky_pref variants, and a third argument
# `sticky` or `locked`). The modifier must be captured separately or it lands in the value: without
# the optional group, `pref("general.smoothScroll", true, sticky);` parsed as the value
# `'true, sticky'`, which overrode StaticPrefList's correct `true` and would read as a value change
# the moment a commit added or removed the modifier. It is worth keeping rather than discarding --
# a `locked` pref cannot be changed by a user, which bears on whether a flip reaches anyone.
JS_PREF_RE = re.compile(
    r"""^\s*(?:sticky_)?pref\(\s*["']([^"']+)["']\s*,\s*(.+?)\s*"""
    r"""(?:,\s*(sticky|locked)\s*)?\)\s*;""")
# StaticPrefList.yaml entry fields
YAML_NAME_RE = re.compile(r"^\s*-\s+name:\s*(\S+)")
YAML_VALUE_RE = re.compile(r"^\s*value:\s*(.+?)\s*$")
YAML_TYPE_RE = re.compile(r"^\s*type:\s*(\S+)")
YAML_MIRROR_RE = re.compile(r"^\s*mirror:\s*(\S+)")
DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)\s*(.*)$")
UNDEF_RE = re.compile(r"^\s*#\s*undef\s+(\w+)")
COND_RE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b\s*(.*)$")
# StaticPrefList is YAML, so `#` also starts a prose comment -- and some comments
# begin with the word "if". Treating one as a directive opens a conditional that
# never closes, silently marking every later entry inactive. (Real case: line 13705,
# "# if disabled and when about:webrtc is not in the foreground history data", hid
# ~1,000 prefs including network.webtransport.enabled.) Indented *real* directives do
# exist, so column position alone cannot decide it -- validate the expression shape.
PP_EXPR_OK = re.compile(r"^[\w\s()!&|<>=.+\-*/]*$")
PP_HAS_SYMBOL = re.compile(r"\bdefined\b|[A-Z_]{2,}|\d")


def _strip_trailing_comment(rest: str) -> str:
    """Real directives here carry trailing comments in three styles:
    `#endif /* XP_DARWIN */`, `#endif // note`, and `#endif  # MOZ_WEBRTC`.
    """
    rest = re.sub(r"/\*.*?\*/", "", rest)
    rest = re.sub(r"//.*$", "", rest)
    rest = re.sub(r"#.*$", "", rest)
    return rest.strip()


def is_directive(kind: str, rest: str) -> bool:
    """Is this a real preprocessor directive rather than a prose comment?"""
    rest = _strip_trailing_comment(rest)
    if kind in ("else", "endif"):
        return rest == ""
    if kind in ("ifdef", "ifndef"):
        return bool(re.fullmatch(r"\w+", rest))
    # if / elif: must look like an expression and reference a real symbol.
    return bool(rest) and bool(PP_EXPR_OK.match(rest)) and bool(PP_HAS_SYMBOL.search(rest))
AT_DEFINE_RE = re.compile(r"^@(\w+)@$")

# Preprocessors created during a run, so unparseable guards can be reported once at
# the end rather than swallowed per-expression.
_EVAL_FAILURES: list = []

# StaticPrefList entries seen versus kept, per parse, collected the same way. A guard count is not a
# consequence: two unevaluable guards at FIREFOX_110 hid 1,018 of 2,098 preferences, and "2 guards"
# is not something a reader turns into "half the file".
_SPL_ENTRY_STATS: list = []


class CannotEvaluate(Exception):
    """A `#if` expression that does not evaluate -- which means it is probably not a `#if`.

    `is_directive` decides whether a `#`-line is a real directive from its shape alone, and shape
    cannot separate a guard from prose that happens to read like one: `# if and only if ATOK is
    active TIP.` passes, because ATOK satisfies the "references a symbol" test. Treated as a false
    guard it opens a block with no `#endif`, and everything after it in the file goes inactive --
    measured at 1,018 of 2,098 preferences lost. Treated as prose it costs nothing, because a real
    guard that does not evaluate would be a Gecko build failure.
    """

# Channel guard truth tables. EARLY_BETA_OR_EARLIER is true on nightly and the
# first half of a beta cycle; RELEASE_OR_BETA is the complement of nightly.
CHANNELS = {
    "nightly": {
        "NIGHTLY_BUILD": True,
        "EARLY_BETA_OR_EARLIER": True,
        "RELEASE_OR_BETA": False,
        "MOZ_DEV_EDITION": False,
        "MOZILLA_OFFICIAL": True,
        "DEBUG": False,
    },
    "beta-early": {
        "NIGHTLY_BUILD": False,
        "EARLY_BETA_OR_EARLIER": True,
        "RELEASE_OR_BETA": True,
        "MOZ_DEV_EDITION": False,
        "MOZILLA_OFFICIAL": True,
        "DEBUG": False,
    },
    "beta-late": {
        "NIGHTLY_BUILD": False,
        "EARLY_BETA_OR_EARLIER": False,
        "RELEASE_OR_BETA": True,
        "MOZ_DEV_EDITION": False,
        "MOZILLA_OFFICIAL": True,
        "DEBUG": False,
    },
    "release": {
        "NIGHTLY_BUILD": False,
        "EARLY_BETA_OR_EARLIER": False,
        "RELEASE_OR_BETA": True,
        "MOZ_DEV_EDITION": False,
        "MOZILLA_OFFICIAL": True,
        "DEBUG": False,
    },
}

PLATFORMS = {
    "win": {"XP_WIN": True, "XP_MACOSX": False, "XP_DARWIN": False, "MOZ_WIDGET_GTK": False,
            "ANDROID": False, "MOZ_WIDGET_ANDROID": False, "XP_UNIX": False,
            "UNIX_BUT_NOT_MAC": False, "XP_IOS": False, "MOZ_WIDGET_UIKIT": False},
    "mac": {"XP_WIN": False, "XP_MACOSX": True, "XP_DARWIN": True, "MOZ_WIDGET_GTK": False,
            "ANDROID": False, "MOZ_WIDGET_ANDROID": False, "XP_UNIX": True,
            "UNIX_BUT_NOT_MAC": False, "XP_IOS": False, "MOZ_WIDGET_UIKIT": False},
    "linux": {"XP_WIN": False, "XP_MACOSX": False, "XP_DARWIN": False, "MOZ_WIDGET_GTK": True,
              "ANDROID": False, "MOZ_WIDGET_ANDROID": False, "XP_UNIX": True,
              "UNIX_BUT_NOT_MAC": True, "XP_IOS": False, "MOZ_WIDGET_UIKIT": False},
    "android": {"XP_WIN": False, "XP_MACOSX": False, "XP_DARWIN": False, "MOZ_WIDGET_GTK": False,
                "ANDROID": True, "MOZ_WIDGET_ANDROID": True, "XP_UNIX": True,
                "UNIX_BUT_NOT_MAC": False, "XP_IOS": False, "MOZ_WIDGET_UIKIT": False},
}

DESKTOP = ["win", "mac", "linux"]


def git(repo: Path, *args: str) -> str:
    try:
        return trainlib.git(repo, *args)
    except RuntimeError as e:
        sys.exit(f"error: {e}")


def show(repo: Path, rev: str, path: str) -> str:
    """Read a file at a revision. Never touches the working tree."""
    return git(repo, "show", f"{rev}:{path}")


def show_optional(repo: Path, rev: str, path: str) -> str | None:
    """`show`, returning None when the path does not exist at that revision.

    Only a genuine "no such path" is turned into None. Every other git failure -- a bad revision, a
    missing binary, a corrupt object store -- propagates, because reporting it as an empty file
    makes each preference in the commit look newly added or removed with nothing on screen to
    distinguish that from the truth.
    """
    try:
        return trainlib.git(repo, "show", f"{rev}:{path}")
    except RuntimeError as e:
        msg = str(e)
        if "does not exist" in msg or "exists on disk, but not in" in msg:
            return None
        sys.exit(f"error: {e}")


class Preprocessor:
    """Just enough C preprocessor to evaluate the guards these two files use."""

    def __init__(self, symbols: dict[str, bool]):
        self.symbols = dict(symbols)
        self.defines: dict[str, str] = {}
        # Expressions this evaluator could not parse. Treating a failure as False
        # silently deactivates the whole #if block and every preference inside it,
        # which produces confidently wrong gating verdicts. A comparable silent
        # failure once hid ~1,000 preferences and was only noticed because one
        # lookup happened to return NOT FOUND, so make it audible instead.
        self.eval_failures: list[str] = []

    def _defined(self, name: str) -> bool:
        if name in self.symbols:
            return bool(self.symbols[name])
        return name in self.defines

    def _eval(self, expr: str) -> bool:
        e = expr.strip()
        if not e:
            return False
        # Strip trailing comments.
        e = re.sub(r"//.*$", "", e).strip()
        # defined(X) / defined X
        e = re.sub(r"defined\s*\(\s*(\w+)\s*\)", lambda m: str(self._defined(m.group(1))), e)
        e = re.sub(r"defined\s+(\w+)", lambda m: str(self._defined(m.group(1))), e)
        # Bare identifiers that survived: treat a known symbol as its truth value.
        def ident(m: re.Match) -> str:
            w = m.group(0)
            if w in ("True", "False", "and", "or", "not"):
                return w
            if w in self.symbols:
                return str(bool(self.symbols[w]))
            if w in self.defines:
                v = self.defines[w].strip()
                return "True" if v not in ("", "0", "false", "False") else "False"
            return "False"

        e = re.sub(r"[A-Za-z_]\w*", ident, e)
        e = e.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
        try:
            return bool(eval(e, {"__builtins__": {}}, {}))  # noqa: S307 - fixed vocabulary
        except Exception:
            self.eval_failures.append(expr.strip()[:100])
            raise CannotEvaluate(expr.strip()[:100])

    def run(self, text: str):
        """Yield (line, active) for every line, honouring #if/#else/#endif nesting."""
        stack: list[dict] = []  # each: {"active": bool, "taken": bool, "parent": bool}
        for line in text.splitlines():
            m = COND_RE.match(line)
            if m and is_directive(m.group(1), m.group(2)):
                kind, rest = m.group(1), m.group(2)
                parent = stack[-1]["active"] if stack else True
                if kind in ("ifdef", "ifndef", "if"):
                    if kind == "ifdef":
                        val = self._defined(rest.strip().split()[0]) if rest.strip() else False
                    elif kind == "ifndef":
                        val = not (self._defined(rest.strip().split()[0]) if rest.strip() else True)
                    else:
                        try:
                            val = self._eval(rest)
                        except CannotEvaluate:
                            # Prose, not a guard -- see CannotEvaluate. Fall through and treat the
                            # line as ordinary text, opening no block.
                            continue
                    stack.append({"active": parent and val, "taken": val, "parent": parent})
                elif kind == "elif":
                    if stack:
                        f = stack[-1]
                        try:
                            val = self._eval(rest)
                        except CannotEvaluate:
                            continue
                        f["active"] = f["parent"] and (not f["taken"]) and val
                        f["taken"] = f["taken"] or val
                elif kind == "else":
                    if stack:
                        f = stack[-1]
                        f["active"] = f["parent"] and not f["taken"]
                        f["taken"] = True
                elif kind == "endif":
                    if stack:
                        stack.pop()
                continue

            active = stack[-1]["active"] if stack else True
            if active:
                dm = DEFINE_RE.match(line)
                if dm:
                    self.defines[dm.group(1)] = dm.group(2)
                    continue
                um = UNDEF_RE.match(line)
                if um:
                    self.defines.pop(um.group(1), None)
                    continue
            yield line, active


def parse_static_pref_list(text: str, symbols: dict[str, bool]) -> dict[str, dict]:
    """Return {pref_name: {value, type, mirror}} as this (channel, platform) sees it."""
    pp = Preprocessor(symbols)
    _EVAL_FAILURES.append(pp)
    prefs: dict[str, dict] = {}
    cur: dict | None = None
    seen = 0
    for line, active in pp.run(text):
        if YAML_NAME_RE.match(line):
            seen += 1
        if not active:
            # An inactive entry means the pref does not exist for this build at all.
            if YAML_NAME_RE.match(line):
                cur = None
            continue
        nm = YAML_NAME_RE.match(line)
        if nm:
            cur = {"name": nm.group(1), "value": None, "type": None, "mirror": None}
            prefs[nm.group(1)] = cur
            continue
        if cur is None:
            continue
        vm = YAML_VALUE_RE.match(line)
        if vm:
            raw = vm.group(1).strip()
            am = AT_DEFINE_RE.match(raw)
            if am:
                # @IS_NIGHTLY_BUILD@ -> resolve through the #define table.
                resolved = pp.defines.get(am.group(1))
                cur["value"] = resolved.strip() if resolved is not None else raw
                cur["via_define"] = am.group(1)
            else:
                cur["value"] = raw
            continue
        tm = YAML_TYPE_RE.match(line)
        if tm:
            cur["type"] = tm.group(1)
            continue
        mm = YAML_MIRROR_RE.match(line)
        if mm:
            cur["mirror"] = mm.group(1)
    _SPL_ENTRY_STATS.append((seen, len(prefs)))
    return prefs


def parse_firefox_js(text: str, symbols: dict[str, bool]) -> dict[str, dict]:
    """{pref: {value, modifier}} -- shaped like parse_static_pref_list, so callers merge alike."""
    pp = Preprocessor(symbols)
    prefs: dict[str, dict] = {}
    for line, active in pp.run(text):
        if not active:
            continue
        m = JS_PREF_RE.match(line)
        if m:
            prefs[m.group(1)] = {"value": m.group(2).strip(), "modifier": m.group(3)}
    return prefs


def effective_defaults(repo: Path, rev: str, channels: list[str], platforms: list[str]) -> dict:
    """{pref: {(channel, platform): value}} merging StaticPrefList with the app-level pref file.

    **The app-level file is per product, not shared.** `browser/app/profile/firefox.js` ships with
    desktop Firefox; `mobile/android/app/geckoview-prefs.js` ships with GeckoView and Fenix. Reading
    only the desktop one leaves Android's real defaults invisible -- 40 of the 145 preferences set in
    `geckoview-prefs.js` override a StaticPrefList default, so an Android answer taken from
    StaticPrefList alone is wrong for those. Applying the desktop file to Android would be equally
    wrong in the other direction, which is why the choice is keyed on the platform.

    This is also how a feature gets turned on for Android specifically without any visible change to
    a shared default -- see `reference/release-notes/gating.md`.
    """
    spl_text = show(repo, rev, STATIC_PREF_LIST)
    all_text = show(repo, rev, ALL_JS)
    app_text = {
        "desktop": show(repo, rev, FIREFOX_JS),
        "android": show_optional(repo, rev, GECKOVIEW_JS) or "",
    }
    app_file = {"desktop": FIREFOX_JS, "android": GECKOVIEW_JS}
    table: dict[str, dict[tuple[str, str], str]] = collections.defaultdict(dict)
    meta: dict[str, dict] = {}

    def apply(parsed: dict, label: str, ch: str, plat: str) -> None:
        """Later files win, and every file a preference appears in is named in its source.

        Compare the same string that gets appended: testing a full path while appending the
        basename meant the guard never matched, so the label grew one copy per configuration.
        """
        for name, info in parsed.items():
            table[name][(ch, plat)] = info["value"]
            if name in meta:
                if label not in meta[name]["source"]:
                    meta[name]["source"] = f"{meta[name]['source']} + {label}"
            else:
                meta[name] = {"source": label, "type": None,
                              "mirror": None, "via_define": None}
            if info.get("modifier"):
                meta[name]["modifier"] = info["modifier"]

    for ch in channels:
        for plat in platforms:
            symbols = {**CHANNELS[ch], **PLATFORMS[plat]}
            kind = "desktop" if plat in DESKTOP else "android"
            for name, info in parse_static_pref_list(spl_text, symbols).items():
                table[name][(ch, plat)] = info["value"]
                meta.setdefault(name, {"source": "StaticPrefList", "type": info.get("type"),
                                       "mirror": info.get("mirror"),
                                       "via_define": info.get("via_define")})
            # all.js is the shared base and applies to every product; the app-level file then
            # overrides both for the product that ships it.
            apply(parse_firefox_js(all_text, symbols), "all.js", ch, plat)
            apply(parse_firefox_js(app_text[kind], symbols),
                  app_file[kind].rsplit("/", 1)[-1], ch, plat)
    return {"table": table, "meta": meta}


def summarize_values(vals: dict[str, str | None], channels: list[str],
                     platforms: list[str]) -> str | None:
    """One display value for a per-config mapping; None only when absent everywhere.

    Labels the smallest dimension that explains the split -- `64 (mac,linux)` rather than
    enumerating eight channel/platform pairs -- because the usual reason a value varies is a
    single `#ifdef` on one axis.
    """
    keys = [f"{c}/{p}" for c in channels for p in platforms]
    distinct: list[str | None] = []
    for k in keys:
        if vals[k] not in distinct:
            distinct.append(vals[k])
    if len(distinct) == 1:
        return distinct[0]

    def label(v: str | None) -> str:
        where = {k for k in keys if vals[k] == v}
        plats = [p for p in platforms if all(f"{c}/{p}" in where for c in channels)]
        if where == {f"{c}/{p}" for c in channels for p in plats}:
            return ",".join(plats)
        chans = [c for c in channels if all(f"{c}/{p}" in where for p in platforms)]
        if where == {f"{c}/{p}" for c in chans for p in platforms}:
            return ",".join(chans)
        return ",".join(k for k in keys if k in where)

    return " / ".join(f"{'absent' if v is None else v} ({label(v)})" for v in distinct)


def changed_prefs(before: dict, after: dict, channels: list[str],
                  platforms: list[str]) -> dict[str, dict]:
    """What differs between two fully-resolved endpoint tables, ignoring churn between.

    This compares parsed pref tables rather than reading a git diff. The diff approach it
    replaced recovered a changed `value:` line's pref name by scanning backwards through diff
    *context* for the enclosing `- name:`, which had two failure modes:

    1. **Silent drop** when the name fell outside the context window. Bug 2060067 raised
       `network.http.http3.max_gso_segments` from 10 to 64 and was missed entirely: an
       `#ifdef XP_WIN` branch plus comment lines put `- name:` two lines above a `--unified=6`
       hunk, so the change was discarded with no warning.
    2. **Silent misattribution** — the pending name persisted across entries within a hunk, so a
       value change could be credited to the wrong preference.

    Both are worst exactly where it matters most: `#ifdef`-wrapped entries are the
    platform- and channel-varying ones, i.e. the ones most likely to carry a real gating change.
    """
    keys = [f"{c}/{p}" for c in channels for p in platforms]

    def per_config(table: dict, name: str) -> dict[str, str | None]:
        vals = table.get(name, {})
        return {f"{c}/{p}": vals.get((c, p)) for c in channels for p in platforms}

    out: dict[str, dict] = {}
    for name in set(before) | set(after):
        b, a = per_config(before, name), per_config(after, name)
        if b == a:
            continue
        out[name] = {
            "before": summarize_values(b, channels, platforms),
            "after": summarize_values(a, channels, platforms),
            "before_by_config": b,
            "after_by_config": a,
            "varies_by_config": len(set(b.values())) > 1 or len(set(a.values())) > 1,
        }
    return out


def bucket_of(rec: dict) -> str:
    """Classify a change per-configuration, so a platform-specific flip is still a flip.

    The scalar before/after summary is lossy for `#ifdef`-varying preferences; these read the
    per-config values instead. First match wins, so a record lands in exactly one bucket.
    """
    b, a = rec["before_by_config"], rec["after_by_config"]
    keys = list(b)
    if all(a[k] is None for k in keys):
        return "removed"
    if all(b[k] is None for k in keys) and any(truthy(a[k]) for k in keys):
        return "new_on"
    if any(b[k] is not None and not truthy(b[k]) and truthy(a[k]) for k in keys):
        return "flips"
    if any(truthy(b[k]) and a[k] is not None and not truthy(a[k]) for k in keys):
        return "unflips"
    return "other"


def spl_entries(text: str) -> dict[str, str]:
    """{pref name: raw entry text} from StaticPrefList, structurally and without preprocessing.

    Attribution only needs to know *whether* an entry changed, so this deliberately skips the
    `#ifdef` evaluation `parse_static_pref_list` does: a change inside any branch still shows up
    as a change to the enclosing entry's text, on every configuration at once.
    """
    entries: dict[str, str] = {}
    name, buf = None, []
    for line in text.splitlines():
        nm = YAML_NAME_RE.match(line)
        if nm:
            if name is not None:
                entries[name] = "\n".join(buf)
            name, buf = nm.group(1), [line]
        elif name is not None:
            buf.append(line)
    if name is not None:
        entries[name] = "\n".join(buf)
    return entries


def js_entries(text: str) -> dict[str, str]:
    """{pref name: every raw line declaring it} from firefox.js, without preprocessing."""
    entries: dict[str, list[str]] = collections.defaultdict(list)
    for line in text.splitlines():
        m = JS_PREF_RE.match(line)
        if m:
            entries[m.group(1)].append(line.strip())
    return {k: "\n".join(v) for k, v in entries.items()}


def attribution_map(repo: Path, start: str, end: str) -> dict[str, list[str]]:
    """{pref: [commit lines]} for every pref whose declaration changed in the window.

    Compares parsed entries either side of each commit rather than reading diff context. The
    previous implementation shared `changed_prefs`' defect -- it recovered the pref name by
    scanning a `--unified=6` hunk for the enclosing `- name:`, so a change whose name fell
    outside that window was attributed to nothing and the pref was reported with
    "no bug in commit subject" even when the bug was right there. Bug 2060067 hit exactly this.

    Pickaxe is still not an option, for the reason it never was: `-S` counts *occurrences* of a
    string and `value: false` -> `value: true` leaves the pref name's count untouched, so the
    flips that matter most are the ones it misses.
    """
    log = git(repo, "log", f"{start}..{end}", "--format=%h %s", "--", *PREF_FILES)
    commits = [ln for ln in log.splitlines() if ln.strip()]

    # One read per (rev, file) even though consecutive commits share versions.
    cache: dict[tuple[str, str], dict[str, str]] = {}

    def entries_at(rev: str, path: str) -> dict[str, str]:
        key = (rev, path)
        if key not in cache:
            text = show_optional(repo, rev, path)
            if text is None:
                text = ""  # the file genuinely did not exist at this revision
            cache[key] = spl_entries(text) if path == STATIC_PREF_LIST else js_entries(text)
        return cache[key]

    out: dict[str, list[str]] = collections.defaultdict(list)
    for line in commits:
        sha = line.split(" ", 1)[0]
        for path in PREF_FILES:
            before = entries_at(f"{sha}^", path)
            after = entries_at(sha, path)
            for name in set(before) | set(after):
                if before.get(name) != after.get(name) and line not in out[name]:
                    out[name].append(line)
    return out


def bug_of(subject: str) -> str | None:
    m = re.search(r"[Bb]ug\s+(\d{5,8})", subject)
    return m.group(1) if m else None


def is_bool_literal(v: str | None) -> bool:
    return v is not None and str(v).strip().lower() in ("true", "false")


def truthy(v: str | None) -> bool:
    return v is not None and str(v).strip().lower() in ("true", "1")


def namespace_gates(pref: str, table: dict, channels: list[str], platforms: list[str]) -> list[str]:
    """State of the `<namespace>.enabled` preferences above this one.

    A flip is only as meaningful as the feature it configures. Real case:
    `browser.smartwindow.mistralRelease` flipped to true on every channel, which reads as
    a strong signal -- but `browser.smartwindow.enabled` is false everywhere, so the flip
    is inert. Checking the flipped preference's own default is not enough.
    """
    parts = pref.split(".")
    out = []
    for i in range(len(parts) - 1, 1, -1):
        gate = ".".join(parts[:i]) + ".enabled"
        if gate == pref or gate not in table:
            continue
        out.append(f"{gate}: {classify(table[gate], channels, platforms)}")
    return out


def classify(values: dict[tuple[str, str], str], channels: list[str], platforms: list[str]) -> str:
    """One-line human summary of where a pref is on (or what it is set to).

    Handles three cases a naive on/off summary gets wrong: non-boolean prefs (an
    integer or string default is not "off"), prefs that are *absent* on some
    channels because their entry sits inside an #ifdef, and prefs that differ by
    platform rather than by channel.
    """
    present = [v for v in values.values() if v is not None]
    if not present:
        return "not present in any build configuration"

    # Non-boolean pref: report the value(s), never on/off.
    if not all(is_bool_literal(v) for v in present):
        distinct = {}
        for ch in channels:
            for plat in platforms:
                distinct.setdefault(str(values.get((ch, plat))), []).append(f"{ch}/{plat}")
        if len(distinct) == 1:
            return f"non-boolean default: {next(iter(distinct))}"
        parts = [f"{v} ({len(w)} configs)" for v, w in distinct.items()]
        return "non-boolean default, varies: " + "; ".join(parts)

    # Boolean: track absent separately from false.
    per_channel: dict[str, object] = {}
    absent_channels = []
    for ch in channels:
        raw = {values.get((ch, p)) for p in platforms}
        if raw == {None}:
            per_channel[ch] = None
            absent_channels.append(ch)
            continue
        vals = {truthy(values.get((ch, p))) for p in platforms}
        if len(vals) == 1:
            per_channel[ch] = next(iter(vals))
        else:
            on = [p for p in platforms if truthy(values.get((ch, p)))]
            per_channel[ch] = f"{', '.join(on)} only"

    live = {ch: v for ch, v in per_channel.items() if v is not None}
    suffix = ""
    if absent_channels:
        suffix = f" (preference absent on {', '.join(absent_channels)})"

    if live and all(v is True for v in live.values()):
        return ("on by default everywhere" if not absent_channels
                else f"on where present{suffix}")
    if live and all(v is False for v in live.values()):
        return f"off by default on all channels{suffix}"
    if live.get("nightly") is True and live.get("release") is False:
        if live.get("beta-early") is True and live.get("beta-late") is False:
            return f"Nightly + early Beta only{suffix}"
        if live.get("beta-early") is True:
            return f"Nightly and Beta only{suffix}"
        return f"Nightly-only{suffix}"
    rendered = []
    for ch, v in per_channel.items():
        if v is None:
            rendered.append(f"{ch}: absent")
        elif v is True:
            rendered.append(f"{ch}: on")
        elif v is False:
            rendered.append(f"{ch}: off")
        else:
            rendered.append(f"{ch}: {v}")
    return "; ".join(rendered)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Detect preference flips and resolve effective defaults per channel."
    )
    p.add_argument("--repo", default=None,
                   help="Gecko checkout (default: saved by watchlist.py check-setup)")
    p.add_argument("--rev", default="origin/main", help="revision to read defaults from")
    p.add_argument("--range", dest="rev_range", default=None, help="explicit START..END")
    p.add_argument("--lookup", default=None,
                   help="comma-separated preference names to resolve (skips flip detection)")
    p.add_argument("--no-fetch", action="store_true", help="skip git fetch origin")
    p.add_argument("--channels", default="nightly,beta-early,beta-late,release")
    p.add_argument("--platforms", default="win,mac,linux,android",
                   help="build configurations to resolve. android is on by default because an "
                        "#ifdef ANDROID default, or an override in geckoview-prefs.js, is how a "
                        "feature ships to Firefox for Android and is otherwise invisible")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args()

    repo = trainlib.resolve_repo(args.repo)

    channels = [c.strip() for c in args.channels.split(",") if c.strip() in CHANNELS]
    platforms = [x.strip() for x in args.platforms.split(",") if x.strip() in PLATFORMS]

    if not args.no_fetch:
        trainlib.fetch_origin(repo, "Defaults below are resolved from a possibly stale "
                                    f"{args.rev}; do not label them verified.")

    print(f"# resolving defaults from {args.rev}", file=sys.stderr)
    eff = effective_defaults(repo, args.rev, channels, platforms)
    print(f"# {len(eff['table'])} preferences resolved across "
          f"{len(channels)}x{len(platforms)} build configurations", file=sys.stderr)
    bad = sorted({e for pp in _EVAL_FAILURES for e in pp.eval_failures})
    if bad:
        # Quantified, because a guard count is not a consequence: two of these once hid 1,018 of
        # 2,098 preferences, and the worst case is the whole rest of the file.
        # The ratio of the maxima is not the maximum ratio: a window comparison resolves both
        # endpoints, so `max(seen)` and `max(seen - kept)` can come from different revisions and
        # understate the one that is actually truncated. Pick the worst single configuration.
        seen_at_worst, kept_at_worst = max(_SPL_ENTRY_STATS, key=lambda sk: (sk[0] - sk[1]) / (sk[0] or 1),
                                      default=(0, 0))
        worst = seen_at_worst - kept_at_worst
        pct = (100 * worst / seen_at_worst) if seen_at_worst else 0
        print(f"# *** WARNING: {len(bad)} preprocessor guard(s) could not be evaluated and were "
              f"treated as prose rather than as a condition.\n"
              f"#     Sanity check: {worst} of {seen_at_worst} StaticPrefList entries ({pct:.0f}%) are "
              f"inactive in the worst configuration. Most of that is normal -- measured across the "
              f"110-155 revisions, platform and channel guards leave 8-10% inactive -- so a much "
              f"larger share means the file was truncated and this run should not be trusted. "
              f"The unevaluable expressions were:", file=sys.stderr)
        for e in bad[:10]:
            print(f"#     {e}", file=sys.stderr)

    if args.lookup:
        names = [n.strip() for n in args.lookup.split(",") if n.strip()]
        results = []
        for n in names:
            vals = eff["table"].get(n)
            if not vals:
                results.append({"pref": n, "found": False})
                continue
            results.append({
                "pref": n, "found": True,
                "summary": classify(vals, channels, platforms),
                "meta": eff["meta"].get(n, {}),
                "values": {f"{c}/{p}": vals.get((c, p)) for c in channels for p in platforms},
            })
        if args.format == "json":
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                if not r["found"]:
                    print(f"{r['pref']}: NOT FOUND at {args.rev}")
                    continue
                m = r["meta"]
                via = f", via @{m['via_define']}@" if m.get("via_define") else ""
                print(f"{r['pref']}")
                print(f"  {r['summary']}")
                print(f"  source: {m.get('source')}{via}"
                      + (f", mirror: {m['mirror']}" if m.get("mirror") else "")
                      # `locked` means a user cannot change it, which decides whether a flip is
                      # something they can opt out of.
                      + (f", {m['modifier']}" if m.get("modifier") else ""))
                for k, v in r["values"].items():
                    print(f"    {k:<20} {v}")
        return

    # Resolve the window.
    if not args.rev_range:
        # Commit dates on firefox-main are non-monotonic, so `rev-list --before=<date>` lands at an
        # arbitrary point in the ancestry -- one measured case covered 29 of a build's 57 bugs. There
        # is no safe default window, so ask for an explicit one instead of inventing a wrong one.
        sys.exit("error: --range START..END is required (a preference delta needs an exact window).\n"
                 "Get boundary commits from: python3 scripts/relnotes/scan-window.py --show-state\n"
                 "Or use --lookup <pref> to resolve current defaults without a window.")
    start, end = args.rev_range.split("..", 1)
    start_desc = git(repo, "log", "-1", "--format=%h %cd", "--date=short", start).strip()
    end_desc = git(repo, "log", "-1", "--format=%h %cd", "--date=short", end).strip()
    print(f"# window {start_desc} .. {end_desc}", file=sys.stderr)

    # Also resolve defaults *at the window end*. `effective now` reads today's tree, which
    # for a historical window can flatly contradict the flip: on the 2026-07-21 window
    # browser.promo.cookiebanners.enabled shows FLIPPED ON while "effective now" says off,
    # because it was flipped back three days later. Scanning forward day by day this never
    # bites; backfilling old windows it always does.
    end_sha = git(repo, "rev-parse", end).strip()
    rev_sha = git(repo, "rev-parse", args.rev).strip()
    if end_sha != rev_sha:
        print(f"# also resolving defaults at the window end ({end_sha[:12]})", file=sys.stderr)
        eff_end = effective_defaults(repo, end_sha, channels, platforms)
    else:
        eff_end = eff  # the window ends at the revision we already resolved

    print(f"# resolving defaults at the window start ({git(repo, 'rev-parse', start)[:12]})",
          file=sys.stderr)
    eff_start = effective_defaults(repo, start, channels, platforms)
    changes = changed_prefs(eff_start["table"], eff_end["table"], channels, platforms)
    attribution = attribution_map(repo, start, end)
    records = []
    for name, ch in sorted(changes.items()):
        vals = eff["table"].get(name, {})
        commits = attribution.get(name, [])
        bugs = sorted({b for b in (bug_of(c) for c in commits) if b})
        records.append({
            "pref": name,
            "before": ch["before"],
            "after": ch["after"],
            "effective": (classify(vals, channels, platforms) if vals
                          else "preference removed (absent at rev)"),
            "effective_at_window_end": (
                classify(eff_end["table"].get(name, {}), channels, platforms)
                if eff_end and eff_end["table"].get(name) else None),
            "values": {f"{c}/{p}": vals.get((c, p)) for c in channels for p in platforms},
            "before_by_config": ch["before_by_config"],
            "after_by_config": ch["after_by_config"],
            "varies_by_config": ch["varies_by_config"],
            "gates": namespace_gates(name, eff["table"], channels, platforms),
            "bugs": bugs,
            "commits": commits,
            "meta": eff["meta"].get(name, {}),
        })

    if args.format == "json":
        print(json.dumps({
            "window": {"start": start, "end": end,
                       "start_desc": start_desc, "end_desc": end_desc},
            "changed": records,
        }, indent=2))
        return

    if not records:
        print(f"No preference default changes between {start_desc} and {end_desc}.")
        return

    # A pref that did not exist before is a new feature landing; a pref that
    # existed and changed default is a feature going live. Different stories.
    grouped: dict[str, list[dict]] = collections.defaultdict(list)
    for r in records:
        grouped[bucket_of(r)].append(r)
    flips, new_on = grouped["flips"], grouped["new_on"]
    unflips, removed, other = grouped["unflips"], grouped["removed"], grouped["other"]

    print(f"Preference changes, {start_desc} .. {end_desc}")
    print(f"Configurations: {' '.join(channels)} x {' '.join(platforms)}"
          + ("" if "android" in platforms else
             "   (android NOT covered -- an Android-only flip would not appear)"))
    print(f"({len(records)} net changes; endpoint diff, so land/backout/re-land is already "
          f"collapsed)\n")

    for title, group in (
        ("FLIPPED ON (existing preference, off -> on) -- the strongest release-note signal", flips),
        ("NEW, DEFAULT ON (preference added enabled)", new_on),
        ("FLIPPED OFF (on -> off)", unflips),
        ("OTHER VALUE CHANGES", other),
        ("REMOVED", removed),
    ):
        if not group:
            continue
        print(f"== {title} [{len(group)}]")
        for r in group:
            bugs = ", ".join(f"bug {b}" for b in r["bugs"]) or "no bug in commit subject"
            print(f"  {r['pref']}")
            print(f"    {r['before']} -> {r['after']}   [{bugs}]")
            for g in r.get("gates", []):
                print(f"    feature gate  {g}")
            at_end = r.get("effective_at_window_end")
            if at_end and at_end != r["effective"]:
                print(f"    at window end: {at_end}")
                print(f"    effective now: {r['effective']}   <-- CHANGED SINCE THIS WINDOW")
            else:
                print(f"    effective now: {r['effective']}")
            for c in r["commits"][:4]:
                print(f"      {c}")
        print()


if __name__ == "__main__":
    main()
