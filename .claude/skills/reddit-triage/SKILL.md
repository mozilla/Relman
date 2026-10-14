---
name: reddit-triage
description: Post-release Reddit triage for a Firefox release. Finds the r/firefox "Firefox NNN released" thread and recent subreddit posts (plus related Firefox subreddits when needed), pulls comment threads, extracts user-reported problems, de-dupes and groups them by topic to spot trends, links each to a matching Bugzilla bug, and buckets them (Act now / Worth a look / New, no ticket / Not actionable). Supports re-runs that report only what is new since the last pull. Use when the user says things like "analyze the reddit thread for the release", "what's reddit saying about the new release", "post-release reddit digest", "reddit triage", "any new ones?", or "let's do this again" after a previous run.
---

# Reddit Triage

After a Firefox release ships, read the r/firefox release thread and the rest of the subreddit for user-reported problems with the new version, and turn them into a short, actionable triage list for release management: what to act on, what already has a ticket, what's trending, and what's noise. Every item carries a **Reddit permalink** and, where one exists, a **matching bug**.

**Scope: read and report only.** Never post or reply on Reddit, and never modify Bugzilla (no comments, flags, needinfos, status changes). When an action is warranted (ask a reporter for details, file a bug, request an uplift), list it under Next steps for the user to take.

## Inputs

- **Release version (`NNN`).** Use the version the user names. Otherwise use the current release from product-details (`https://product-details.mozilla.org/1.0/firefox_versions.json`, `LATEST_FIREFOX_VERSION`; take the major). Derive `NNN-1` (previous release) and `NNN+1` / `NNN+2` (beta / nightly) from it. Never carry a version number over from a previous run or from examples.
- **Window.** Default: release time → now. Major releases go out around 13:00 UTC (9am ET); the release thread's creation time is a good anchor. The user works in US Eastern time, so "since 9am" means 13:00 UTC (12:00 UTC outside daylight saving). Convert, and state the window in UTC in the output.
- **Re-run mode.** If this conversation already contains a run, don't redo the full analysis; report only what's new since the last pull plus status changes on tracked bugs (see "Re-runs").

## Acquiring Reddit data

These constraints have held on every run; build around them.

1. **Only the Atom feeds work.** `reddit.com/.../*.json`, `api.reddit.com` and `old.reddit.com` return 403/302 to curl regardless of User-Agent. Use `.rss`:
   - Newest subreddit posts: `https://www.reddit.com/r/<sub>/new/.rss?limit=100` (about 100 posts, roughly 1.5–3 days on r/firefox).
   - A post with its comments: `https://www.reddit.com/r/<sub>/comments/<post_id>/.rss?sort=new&limit=500`
   - One comment with its parents (for replies that only make sense with what they answer): `https://www.reddit.com/r/<sub>/comments/<post_id>/_/<comment_id>/.rss?context=5`
   - Search: `https://www.reddit.com/r/<sub>/search/.rss?q=<terms>&restrict_sr=1&sort=new&limit=100`
2. **Rate limiting is aggressive.** Expect HTTP 429 on roughly every other request, with a 0-byte body, so check status *and* size. Fetch sequentially with ~20s between requests and retry 429s after ~22s, up to 10 times. Run fetch loops with `run_in_background` (foreground `sleep` is blocked) and wait for the completion notification rather than polling. A browser-like UA works:
   ```sh
   UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:140.0) Gecko/20100101 Firefox/140.0"
   fetch(){ for i in $(seq 1 10); do code=$(curl -s -A "$UA" -o "$2" -w '%{http_code}' "$1"); \
     [ "$code" = 200 ] && [ "$(wc -c <"$2")" -gt 500 ] && { echo "ok $2"; return; }; sleep 22; done; echo "FAILED $1"; }
   ```
3. **Parsing.** Each Atom `<entry>` has `title`, `updated`, `author/name`, `link@href` (the permalink) and `content` (HTML-escaped body). Strip tags, unescape, and drop the "submitted by" trailer. The comment ID is the last path segment of the permalink (`.../pgh57k8/`); keep the set of seen IDs, since that is how re-runs find new items. A few lines of inline `python3 -I` ElementTree is enough. Don't build a classifier; read the text yourself.
4. **Scratch goes in `mktemp -d`**, never in this repo or the Gecko checkout. Keep the parser in a different temp directory from the downloaded feeds and pass feed paths as arguments.
5. **Portability.** The snippets assume a bash-compatible shell: macOS, Linux, WSL, or Git Bash (which Claude Code uses on native Windows and which ships `curl`, `mktemp`, `seq` and `wc`). The User-Agent only needs to look like a real browser; it doesn't have to match the OS you run on. On Windows, Python is often `python` or `py` rather than `python3`, so check with `command -v python3 python py` and use whichever exists. Under PowerShell, translate the snippets rather than running them as written.

### Finding the release thread

Usually titled **"Firefox NNN.0 released"**, often posted by u/kjoonlee; dot releases get their own ("Firefox NNN.0.1 released"). Look in `r/firefox/new/.rss` first; if the thread is older than the feed window, search for `"NNN.0 released"`. If several threads qualify (megathread, cross-post, Android release), use all of them and say so. If none is found, say so and run on subreddit posts alone.

### Related subreddits

r/firefox is the primary source. Check related Firefox subreddits when the situation calls for it, and say which ones you read:
- A Firefox-specific community for the affected area, e.g. r/FirefoxCSS when a UI change breaks userChrome customizations, or a Firefox-for-Android community when Android reports cluster.
- A subreddit a report links to or points at, e.g. a distro subreddit (r/openSUSE, r/Fedora, r/archlinux) when a problem looks like packaging or an upstream desktop bug.

Confirm a subreddit exists (its `new/.rss` returns entries) before relying on it, keep to its posts from the window that mention Firefox or version `NNN`, and apply the same rate-limit discipline; every extra subreddit costs several fetches.

### What to collect

1. **All comments in the release thread(s).** On a very busy thread the Atom feed may not include everything; if replies look missing, also fetch `sort=top` and merge.
2. **All subreddit posts in the window** from `new/.rss`, plus a search for `NNN` over the last week. Posts that explicitly mention the new version count even if slightly older.
3. **Comment threads for every post that looks like a problem report** (skip questions, add-on promotions and "how do I…" support). Comments are where the diagnosis is: they separate real regressions from moved settings, extensions and non-Firefox causes, and they carry "works for me" counter-reports.
4. **Parent context for meaningful replies**, especially from Mozilla engineers and contributors. Their Reddit names often match Bugzilla nicks (u/pehrsons is :pehrsons), and they may mention a Nimbus mitigation, a study, or a planned dot release. Fetch with `context=5` to know which report they're answering.

## Extracting and grouping reports

Read everything and extract each **distinct problem report**. Ignore praise, feature debates, "Mozilla is Google" arguments, benchmark bragging, karma-farming and pure support questions.

For each report, note: permalink, username, symptom in the user's terms, platform/OS/version if given, what they've ruled out (troubleshoot mode, new profile, extensions off, rolled back to `NNN-1`), and any counter-reports in replies.

**De-dupe and group by topic.** Grouping is how a trend shows up.
- Merge reports of the same symptom; list every permalink under one item.
- **Count distinct users, not posts.** One user posting in the thread and in their own post is one report.
- Note whether a group is growing across pulls ("now 3 users, was 1").
- Weigh the evidence. "Rolling back to `NNN-1` fixes it" and "fails in a new profile or troubleshoot mode" are strong regression signals. "Lately" or "last few versions" with no version is weak.

**Rule out non-Firefox and non-regression causes before escalating.** Seen in past runs:
- Upstream desktop/OS bugs, e.g. a GNOME/mutter change that broke GTK3 copy/paste on Wayland.
- Security software intercepting traffic (Kaspersky and PR_CONNECT_RESET_ERROR).
- Third-party extensions (password managers hanging pages, tab-group add-ons).
- Settings that moved or are opt-in, e.g. theme colourways under New Tab → Customise.
- `privacy.resistFingerprinting` side effects.
- Distro packaging lag (dnf needing `--refresh`, Snap, Flatpak).
- Google Play's staged Android rollout ("I don't have it yet").
- Issues from earlier versions that only now get discussed.

## Matching to Bugzilla

Use REST to list and search bugs, and the `moz` MCP (`get_bugzilla_bug` or `@moz:bugzilla://bug/{id}`) to read a bug's description, comments and history. `buglist.cgi` is behind a WAF; don't scrape it.

1. **Known problems in release `NNN` first.** These explain the most Reddit reports, and you can pull them before reading Reddit:
   - `cf_status_firefoxNNN=affected` with `keywords=regression`, created since the version reached beta
   - `cf_tracking_firefoxNNN` is `+` or `?`
   - summaries containing the version ("Firefox NNN+", "since NNN", "release: <date>")
   ```
   https://bugzilla.mozilla.org/rest/bug?f1=cf_status_firefoxNNN&o1=equals&v1=affected&chfield=%5BBug%20creation%5D&chfieldfrom=<date>&include_fields=id,summary,status,resolution,severity,product,component,regressed_by,cf_status_firefoxNNN,cf_tracking_firefoxNNN&limit=100
   ```
2. **Keyword searches per topic group:** the site (`prime video`, `twitch`, `github`), the feature (`haptic`, `container`, `subpixel`), or the symptom (`scroll top`, `clipboard`, `startup hang`). Use `short_desc=<words>&short_desc_type=allwordssubstr|anywordssubstr&creation_time=<~6 weeks back>`, or `quicksearch=` for broader sweeps. Include UNCONFIRMED bugs: users often file the same report themselves and sometimes link it in the Reddit post.
3. **Check that the bug covers the same symptom on the same platform** before calling it a match. If unsure, label it a "possible match" and say why. Never force one.
4. **Give each matched bug a status line:**
   - status and resolution, severity
   - `cf_status_firefox` for `NNN`, `NNN+1`, `NNN+2`
   - `regressed_by`, with the version-timing guardrail: the regressor must have shipped in `NNN` or earlier
   - uplift flags on the patches (`approval-mozilla-beta`, `approval-mozilla-release`)
   - assignee and any open needinfo

   Call out the gaps that matter for release triage:
   - **fixed on central/beta while `NNN` is still `affected` with no `approval-mozilla-release?`**
   - **`wontfix` for `NNN` despite real user reports**
   - **no severity on a confirmed regression**
   - **an unanswered needinfo to the regressor's author**

   Follow `blocks`/`depends_on`: a fix sometimes exposes a second bug that also affects the release.
5. **Note release regressions found while matching that Reddit hasn't raised yet**, if they're user-visible (e.g. a window-focus or input behaviour change marked `affected` for `NNN`). Reddit tends to catch up with these.

## Output format

Open with one line: pull time (UTC), time since release, and what was read (e.g. "Pulled 12:16 UTC, ~23h after release; 120 thread comments and 30 posts read; r/firefox only"). Then these sections, skipping any that are empty:

1. **Act now.** Confirmed or very likely regressions in `NNN` with real user impact: a broken major site, startup hangs or crashes, data loss, a core function broken. For each: what users see, Reddit links (with distinct-user count), the bug and its status line, and **the specific gap** (e.g. "release still `affected`, only a beta uplift approved").
2. **Worth a look, with a ticket.** Real and matched to a bug, but lower urgency, or the ticket needs triage (no severity, wrong component, UNCONFIRMED with several "me too"s).
3. **New, no ticket.** Plausible version-specific reports with nothing filed. Give a confidence level and what's missing (OS, crash IDs, about:support, a profile). Suggest filing only when there's enough to act on.
4. **Not actionable / not Firefox.** A table of *Report (link)* and *Verdict*: upstream cause, extension, setting or working as intended, support question, too vague, or not a regression.
5. **Earlier versions, context only** (optional). Complaints from before the window that are about `NNN-1`.
6. **Trending** (optional). Any topic with two or more distinct users, or growing across pulls, even if each report alone looks weak.
7. **Tracked bugs** (re-runs). What changed on each bug from earlier pulls: new comments, landings, flags, status.
8. **Also in Bugzilla, not yet on Reddit** (optional).
9. **Next steps.** A numbered list of concrete actions for the user: request a release uplift, ask a reporter for details, reconsider a wontfix, set a severity, file a bug.

Formatting:
- Link comments with their permalinks (`.../comments/<post>/<slug>/<comment_id>/`) and posts with theirs.
- Refer to bugs as `bug NNNNNN`, with the summary in quotes the first time.
- Keep each item to a few lines.
- Match confidence to evidence: "likely", "possible", "unconfirmed".

## Re-runs

When the user asks "any new ones?", "let's do this again" or similar in the same conversation:
- Re-fetch the release thread (`sort=new`) and `new/.rss` (and any related subreddits already in use). Diff against the comment and post IDs already seen, and fetch comment threads only for new problem posts.
- **Always re-check tracked bugs** with one REST call (status, `cf_status_*`, severity, `last_change_time`) plus attachment flags; read new comments with `comment?new_since=<last pull>`.
- Update the groups. If a new report joins an existing group, say so and update the user count. If a reply resolves an earlier item (e.g. "the gradient is a colourway setting"), move it to Not actionable and say why.
- Keep re-run output short: new reports in the same buckets, then Tracked bugs, then Next steps. If nothing meaningful changed, say so in a couple of lines.
- Timing: the first few hours after release are thin and still dominated by `NNN-1` talk. The next morning, after European and US traffic, is the first pull with real signal. Suggest that when a re-run turns up little.

## Caveats to state when they apply

- The Atom feed may not have included every comment on a busy thread.
- A feed couldn't be fetched after repeated 429s (name it).
- The `moz` MCP wasn't available, so REST was used for bug detail.
- A bug match is a best guess rather than confirmed.
