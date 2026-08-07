# Reading Bugzilla, Phabricator, and the tree

Shared conventions for the release-note skills (and compatible with `triage-user-bugs`). These are
constraints of the environment, not preferences — working around them wastes a lot of time.

## The three hard constraints

1. **`buglist.cgi` is behind a bot-challenge WAF.** The HTML page *and* `ctype=csv` return a
   JavaScript "Client Challenge" / "a required part of this site couldn't load" page through
   WebFetch or curl — not data. Do not try to scrape it. Use the REST API, which is not challenged.

2. **The `moz` MCP fetches a bug by id but cannot search.** So: **REST for enumeration, MCP for
   detail.** That split is the whole rule.

3. **REST cannot evaluate `%group.*%` pronouns.** Queries excluding reporters via
   `%group.editbugs%` return HTTP 400. (Relevant mainly to `triage-user-bugs`, which documents the
   `bug_status=UNCONFIRMED` proxy.)

## Enumeration (REST)

Batch ids at roughly **120 per call** and request only the fields you need:

```
curl -s "https://bugzilla.mozilla.org/rest/bug?id=<comma,ids>&include_fields=id,summary,status,resolution,product,component,keywords,type,flags,whiteboard,blocks,depends_on,cf_status_firefox<N>"
```

Useful enumeration queries:

```
# every bug fixed in a given version (the release-note denominator)
curl -s "https://bugzilla.mozilla.org/rest/bug?cf_status_firefox153=fixed&include_fields=id,component&limit=0"

# bugs nominated for a release's notes
curl -s "https://bugzilla.mozilla.org/rest/bug?cf_tracking_firefox_relnote=152%2B&include_fields=id,summary,component"
```

`limit=0` means unlimited — necessary, since the default caps well below the ~2,700 bugs fixed per
cycle.

**Some bugs won't come back.** They're security-restricted. Count them and disclose the count;
don't chase them.

## Turning a query link into ids

1. **Get the parameters.** If it's already a `buglist.cgi?...` URL, read the params off it
   (e.g. `f1=cf_tracking_firefox_relnote&o1=equals&v1=152%2B`). If it's a shortlink (`mzl.la`),
   WebFetch it — WebFetch won't follow the cross-host redirect but *reports* the destination
   `buglist.cgi?...` URL, which carries the same params.
2. **Rebuild against `/rest/bug`** for the id list.
3. **Fan out through the MCP** for the bugs whose detail you actually need.

## Detail (MCP)

**The scripts cannot call MCP tools** — that is why `scan-window.py`, `bug-detail.py` and the rest
use REST, and for bulk work REST is correct. But when *you* are judging a single awkward candidate
interactively, prefer the MCP: `get_bugzilla_bug(bug_id)` returns the full **change history with
timestamps** in one call, which REST snapshots do not give you.

That history answers questions the funnel otherwise has to infer:

- *When was `relnote-firefox` set, and by whom?* — `cf_tracking_firefox_relnote: '---' → '?'`
- *When did this land, and for which version?* — `cf_status_firefox155: '---' → 'fixed'`
- *Was it uplifted, and when?* — the same transition on an earlier version's flag
- *Has someone already made a call here?* — a flag set long before your scan

There is **no search or buglist tool**; `get_bugzilla_bug` takes one integer bug id. Enumeration is
REST-only, which is the whole reason for the split above.

- **Bug:** `get_bugzilla_bug(bug_id)` or the resource `@moz:bugzilla://bug/{id}` — summary,
  component, status, comments, and the full field-change timeline.
- **Phabricator revision:** read `@moz:phabricator://revision/D{id}` — the patch and its review
  comments. The `D` number is in the bug's commit comments, or in the `Differential Revision:` line
  of the git commit message.

Fall back to `curl -s https://bugzilla.mozilla.org/rest/bug/{id}` plus `/comment` when the MCP
isn't connected or redacts untrusted reporter content — **and say which you used.** Beyond the
enumeration calls above, don't reach for other scraping routes.

## Reading the patch (and why it matters for scoping)

A bug's component or summary may name one operating system while the actual fix is
platform-agnostic — the *cause* can be OS-specific even when the corrected behavior isn't. Don't
narrow a note to the component's platform on that basis.

**The definitive check is the changed file paths:**

- Under `widget/cocoa`, `widget/gtk`, `widget/windows`, `*/mac`, `*/gtk`, `*/win` → genuinely
  that-OS-only.
- Under `browser/`, `toolkit/`, `dom/`, `layout/`, `gfx/` → ships everywhere, regardless of which
  OS the bug was reported on.

Get the diff from Phabricator by default (always available). A local Gecko checkout works too, and
you no longer have to guess whether there is one or where it lives:

```
python3 scripts/relnotes/watchlist.py check-setup
```

prints the resolved clone path (or tells you how to set it) and every script reads the same value from
per-user state. Use that **absolute** path in `git -C <path> show --stat <hash>` — not `~`, not
`cd <path> && git …`, both of which miss the permission allowlist and prompt on every call. See
[`command-forms.md`](command-forms.md).

When you genuinely can't read the diff, prefer the developer's intended scope and keep the note
generic rather than adding a platform qualifier that might be wrong. Raise it as a question.

## Channel mapping — fetch it, never assume it

The version-to-channel mapping shifts about monthly:

```
curl -s https://product-details.mozilla.org/1.0/firefox_versions.json
```

- `FIREFOX_NIGHTLY` → what lands on `firefox-main` today. Call it **N**.
- `LATEST_FIREFOX_DEVEL_VERSION` → Beta (**N-1**).
- `LATEST_FIREFOX_VERSION` → Release (**N-2**).
- `FIREFOX_ESR` / `FIREFOX_ESR_NEXT` → ESR.
- `NEXT_MERGE_DATE` / `LAST_MERGE_DATE` → the cycle boundaries, which define a full-cycle scan
  window.

A change landing now ships to users in **Firefox N**, so its Bugzilla status field is
`cf_status_firefox{N}`.

## Freshness rules (getting these wrong produces confidently-wrong output)

- **Refresh the mirror first:** `git fetch origin main`. A stale local `main` misses the most recent
  day.
- **Read code and preference defaults from `origin/main`, never the working tree.** Use
  `git show origin/main:<path>`. The working tree is often checked out on an unrelated branch or an
  older commit and will show pre-landing values. This is the single most common way to get a gating
  call wrong.
- **Never quote a commit's diff or message as if it were current code.** Patches get reworked,
  reverted, and re-landed; one bug can have three different "Part 1" landings across central, a
  beta uplift, and a dot release.
- **Separate "what shipped in release X" from "what's on main now."** For a specific shipped
  version read that version's code (`git show FIREFOX_<ver>_RELEASE:path`).

## Backouts on the git mirror

Backouts are phrased **`Revert "..."`** (git style), *not* the Mercurial-style "Backed out" —
matching only "Backed out" finds nothing. Match subjects starting with `Revert`, and allow
"Backed out"/"backout" for safety.

But **do not rely on revert text to decide what's dead**: things land, get backed out, and re-land
inside the same window. The authoritative signal is the Bugzilla status. Keep a bug only if it is
`resolution=FIXED` with status `RESOLVED`/`VERIFIED` and `cf_status_firefox{N}` of
`fixed`/`verified` — treating `disabled` as still-landed, since that only means the code sits behind
an off-by-default preference. Note which bugs appeared in a revert so you can double-check their
*final* state.
