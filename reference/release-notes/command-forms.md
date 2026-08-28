# Command forms that don't prompt

Shared by `find-release-note-candidates` and `review-release-notes`, so the rules live in one place
and the two skills cannot drift apart.

**Why this file exists.** An audit of 533 Bash calls across five sessions found **54% would prompt
for permission on a fresh checkout** (27% on the machine where most of the work was done — the
difference was a personal `settings.local.json`, which is why measuring against your own machine
understates the problem). Allowlist additions took the fresh-checkout figure to 30%; the entire
remaining gap is command *form*, not missing entries.

That is the point: **`.claude/settings.json` travels with the repo and a personal
`settings.local.json` does not.** A command written in the matching form works for everyone. A
command that needs a local grant works only for whoever granted it.

**Read a prompt's wording as a risk explanation, not as the mechanism.** Five prompts were observed
on 2026-08-27, each citing something about the command's content — a `cd` before `git`, a brace
containing a quote, a backslash-escaped newline. All five were also plain matching misses, and the one
probe that separates the two settled it: `python3 -c "print({'a': 1})"` carries the same
brace-and-quote the heredoc was flagged for and does **not** prompt, because `Bash(python3 -c:*)`
matches it. So a match suppresses the prompt whatever the content, and the fix for a prompt is still
either a matching form or an entry.

Permission matching is a **literal prefix** per entry, and a compound command needs **every** segment
allowed. Two details confirmed by direct probe on 2026-08-07: an entry whose prefix ends mid-argument
needs the glob form (`Bash(curl -s https://host/*)`, not `:*`), and a read-only utility absent from the
list below prompts like anything else — `stat` does, while `grep`, `cat`, `sed`, `wc`, `ls` and `find`
do not.

**Measuring this needs a fresh session.** Approving a prompt with "don't ask again for this session"
writes nothing to `settings.local.json` and leaves no mark in the transcript, so a long-lived session
accumulates invisible grants and looks progressively better behaved regardless of what the files say.
A conversation running for three days reported zero prompts while issuing a command that provably
prompts in a new session. The percentages below are *static estimates* over Bash calls only — computed
by matching recorded commands against the allowlist, not counts of prompts anyone saw.

## The rules

- **Never `cd`.** 101 calls in the audit opened with it, and each one prompts: `cd` is not
  allowlisted and every segment of a compound must be. Measured 2026-08-27, `cd … && git …` reports
  it as *"changes directory before running git, which can execute untrusted hooks from the target
  directory"*. The working directory persists between calls and is already the repo root; use
  absolute paths outside it, and `git -C <clone>` for the Gecko reads.
- **Invoke the scripts as `python3 scripts/relnotes/<script>.py` from the repository root.** The bare
  `scripts/relnotes/<script>.py` form matches nothing. This was the single biggest source of prompts
  in the first cold run of a candidates pass — 19 of 20 calls.
- **Scratch files go under `/tmp`, by absolute path.** `Read(//tmp/**)`, `Edit(//tmp/**)` and
  `Bash(mkdir -p /tmp/*)` are pre-approved, and it keeps downloaded HTML and JSON out of the working tree. Never `-o` into the
  repo root. Note the rule is spelled `Edit`, not `Write`: file-permission checks resolve every
  file-editing tool through `Edit(path)` rules, so a `Write(...)` entry silently matches nothing.
- **`-s` (or `-sL`) goes immediately before the URL; every other flag goes after it.** The entries are
  `curl -s <host>…`, so the `-s` position is load-bearing — measured 2026-08-07, an identical request
  to an allowlisted host prompted with `curl "<url>" -s -o …` and did not with
  `curl -s <url> -o …`. Write:

  ```
  curl -s "https://www.firefox.com/…" -o /tmp/notes.html -w "%{http_code}\n"     # matches
  curl "https://www.firefox.com/…" -s -o /tmp/notes.html                          # PROMPTS
  curl -s -o /dev/null -w "%{http_code}\n" "<url>"                               # PROMPTS
  ```

- **Always double-quote a URL, on every command, not just `curl`.** Two independent reasons. On zsh --
  the macOS default -- an unquoted `?` is a glob, and zsh *errors* on no match instead of passing the
  word through as bash does, so the command dies before it starts:
  `(eval):1: no matches found: https://bugzilla.mozilla.org/show_bug.cgi?id=2059647`, observed on a
  colleague's machine. And the allowlist carries double-quoted entries, which single quotes miss.
  Everything else measured in this file was measured on bash/Linux; treat platform-specific claims
  here as unverified on macOS until someone runs them there.

  Single quotes miss the double-quoted entries. Allowlisted hosts: `bugzilla.mozilla.org/rest`, `product-details`,
  `hg-edge`, `nucleus`, `whattrainisitnow`, `wiki.mozilla.org`, `www.firefox.com`, `www.mozilla.org`,
  `www-dev.springfield.moz.works`.
- **Nothing else is allowlisted, and the answer is not to add hosts.** `developer.mozilla.org` prompts;
  so do `drafts.csswg.org`, `www.rfc-editor.org`, `connect.mozilla.org` and `play.google.com`, all of
  which the current Nightly notes link to. Release notes reference **152 distinct hosts** across the
  shipped corpus, so a host list is a treadmill. **Fetch through Python instead, which needs no
  per-host approval:** `note-page.py --check-links` for links, and `trainlib.fetch_text(url)` for the
  raw bytes of anything else. Measured 2026-08-28: a `python3 -c` fetch of a non-allowlisted host does
  not prompt, while the correctly-formed `curl -s "<url>" -o /tmp/… -w …` for the same host does.
- **Bug comments, links and flag state need no `curl` at all.** `bug-detail.py <ids> --comments` gives
  comment 0 and the newest; `--comment 16`, `--comment last:5` and `--comment all` give comments in
  full with their line breaks; `see_also` URLs and the always-printed `open needinfo:` line come with
  the default output. Reach for these first — every raw-curl read in the 08-07 pass used the PROMPTS
  form above, and all three in the 08-08 pass existed only because the script could not yet answer
  "the recent comments", "all the comments", or "the see_also URLs". It can now.
- **No shell `for` loops** — 124 segments in the audit. Loop inside Python instead, in one of the
  forms below.
- **A one-liner goes inline as `python3 -c "…"`; anything longer goes to a `/tmp` file and runs from
  there.** Verified 2026-08-09 by tool-call probes in a clean session: `python3 -c "print('plain')"`
  and `python3 -c "print('<li>brackets</li>')"` both ran without a prompt, so the payload's content —
  angle brackets included — is not the problem. Write the file with `Edit`, then
  `python3 /tmp/analyse.py`.
- **`python3 - <<'EOF'` heredocs prompt, and the trigger is the body's content rather than the
  heredoc.** Measured 2026-08-27: three prompted with *"Contains brace with quote character
  (expansion obfuscation)"*, on lines like `{int(s['bug']) for s in …}`. Because it scans the command
  text, `python3 -c` looked exposed too — **it is not: probed 2026-08-27 with the same
  brace-and-quote payload and it did not prompt**, since its entry matches. The heredoc's entry does
  not. Use the `/tmp` file form, whose entry does.
  Reaching for a heredoc because the snippet has loops in it is exactly the case the `/tmp` form
  serves; folding those onto one `-c` line is what mangled the indentation in the 155 pass.
- **Running scratch Python from `/tmp` is pre-approved, since 2026-08-14.** `Read(//tmp/**)` and
  `Edit(//tmp/**)` govern reading and writing those files but not executing them, so
  `python3 /tmp/x.py` used to prompt with no entry matching it. `Bash(python3 /tmp/*)` closes that.
  It is the same literal-prefix-then-glob shape as `Bash(python3 scripts/relnotes/*)`, which the
  passes prove works — a cycle pass makes dozens of script calls without a prompt. Being in the
  shared file, it reaches colleagues too, and matching is against the command string, so the platform
  does not enter into it. Unmeasured on macOS all the same, like everything else here.
- **No `\`-continued chains.** Four `watchlist.py add … && \` chains recording one pass's verdicts
  each prompted on 2026-08-27. The likely mechanism is prefix matching: the command now begins with a
  backslash rather than `python3`, so no entry can match it. Same lesson as "the same command over N
  inputs" below — issue them as separate calls in one message.
- **Anything you write twice belongs in `scripts/relnotes/`** — matched by prefix, and the reason
  `bug-detail.py` and `note-page.py` exist.
- **Measuring any of this requires a tool call.** Commands run with the `!` prefix execute in the
  user's shell and never reach the permission layer, so they cannot prompt regardless of the rules.
  One probe pair was run that way and its "no prompt" half meant nothing.
- **No command substitution.** A `$(…)` subshell cannot be matched against a static prefix, so it
  prompts however well the entry is written — the same reason `cd` does. Measured on the 08-05 pass:
  with everything else covered, the only Bash prompts were three
  `git show --stat $(git log --format=%H <range> --grep=<bug>)` calls and two `curl` calls with flags
  before the URL. For that specific composition use
  `python3 scripts/relnotes/bug-detail.py <bug> --landings A..B`, which does both steps in-process.
- **Gecko reads use the absolute clone path** recorded by `watchlist.py check-setup`, never `~` and
  never `cd`. The path differs per machine, which is why it lives in per-user state and is written to
  the git-ignored `settings.local.json` rather than the shared allowlist.
- **No shell loops.** `for x in …; do …; done` is one Bash call whose body is not a static prefix, so
  it prompts for the same reason `$(…)` does.
- **A whole-version Bugzilla census is one query, not a loop over bug-id ranges.** Measured
  2026-08-14: `include_fields=id&limit=0` on one `cf_status_firefoxN` returns ~2,600 ids in seconds,
  so there is nothing to page or split — asking for full records *in the same query* is what makes it
  slow enough to look like there is. `scan-window.py --cycle N --version N --census` does that query
  and the diff against the window. The ranged-`curl`-with-retry form it replaces cost two of the five
  prompts in the 155 rollup.
- **The same command over N inputs is N calls, not one clever call.** This is the trigger worth
  recognising, because it is when every rule above gets broken at once and it always feels like
  tidiness rather than a shortcut. Both forms on the 08-13 pass came from it: four
  `git rev-list --count` over different tag pairs became one `cd … && echo "$(…)"; echo "$(…)"`, and
  the same `pref-delta.py --lookup` at two tags became a `for` loop. Each of those calls is
  individually allowlisted; collapsed, none of them is. Issue them separately — they can go in one
  message and run in parallel, which is faster than the loop was going to be.

## Deliberately still prompting

Not oversights — leave these alone:

- `git commit`, `git push`, `git add`, `git reset`, `git checkout`, `rm -rf`. Anything that mutates
  the tree or history should be a conscious approval.
- `cd` on its own. Allowlisting it would remove most remaining prompts, but only if the matcher
  genuinely checks every segment of a compound — asserted in the settings comment, not verified, and
  `cd /tmp && rm -rf …` auto-approved is bad enough not to gamble on.
- `time` and `timeout`. Both wrap an arbitrary command, so allowlisting either launders everything
  after it past the allowlist.

## One deliberate exception in the shared file

`Bash(python3 -c:*)` pre-approves arbitrary Python, which is not read-only. Ad-hoc analysis snippets
were the largest single source of prompts (~196 segments) and the only alternative was every user
granting it locally and diverging. Project settings apply **only while working inside this repo**, and
that scope is what makes it acceptable. The same caveat already applied to `sed`, `awk`, `echo` and
`cat`, any of which can write through a shell redirect.

`Bash(python3 /tmp/*)` extends that grant to the multi-line form and adds no capability `-c` did not
already carry. The one real difference: a `/tmp` file could have been written by something other than
the session running it, where a `-c` payload is always authored in the moment.

`Bash(python3 - <<*)` is in the file for the same reason and does **not** reliably work: the prompts
measured on heredocs came from the content scanner above, which an entry cannot satisfy. It stays only
because removing it changes nothing.
