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

- **Never `cd`.** 101 calls in the audit opened with it, and each one prompts because `cd` is not
  allowlisted and every segment of a compound must be. The working directory persists between calls
  and is already the repo root; use absolute paths for anything outside it.
- **Invoke the scripts as `python3 scripts/relnotes/<script>.py` from the repository root.** The bare
  `scripts/relnotes/<script>.py` form matches nothing. This was the single biggest source of prompts
  in the first cold run of a candidates pass — 19 of 20 calls.
- **Scratch files go under `/tmp`, by absolute path.** `Read(//tmp/**)` and `Edit(//tmp/**)` are
  pre-approved, and it keeps downloaded HTML and JSON out of the working tree. Never `-o` into the
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
  shipped corpus, so a host list is a treadmill. When the task is checking links, use
  `note-page.py --check-links`, which fetches through Python and needs no per-host approval.
- **Bug comments, links and flag state need no `curl` at all.** `bug-detail.py <ids> --comments` gives
  comment 0 and the newest; `--comment 16`, `--comment last:5` and `--comment all` give comments in
  full with their line breaks; `see_also` URLs and the always-printed `open needinfo:` line come with
  the default output. Reach for these first — every raw-curl read in the 08-07 pass used the PROMPTS
  form above, and all three in the 08-08 pass existed only because the script could not yet answer
  "the recent comments", "all the comments", or "the see_also URLs". It can now.
- **No shell `for` loops** — 124 segments in the audit. Loop inside Python instead, in one of the
  forms below.
- **Inline Python goes on one line, as `python3 -c "…"`.** Verified 2026-08-09 by tool-call probes in
  a clean session: `python3 -c "print('plain')"` and `python3 -c "print('<li>brackets</li>')"` both
  ran without a prompt, so the payload's content — angle brackets included — is not the problem.
- **`python3 - <<'EOF'` heredocs prompt**, despite `Bash(python3 - <<*)` existing. They interrupted a
  real review pass. The exact trigger is unconfirmed; the likeliest explanation is that
  the body lines are parsed as separate commands, none of which is allowlisted. Whatever the cause,
  fold the snippet onto one `-c` line or put it in a script.
- **`python3 /tmp/x.py` prompts too** — measured. `Read(//tmp/**)` and `Edit(//tmp/**)` govern reading
  and writing those files, not running them, and no `Bash` entry matches `python3 /tmp/...`. `/tmp` is
  still the right place to *hold* scratch Python; running it is not pre-approved.
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

## Deliberately still prompting

Not oversights — leave these alone:

- `git commit`, `git push`, `git add`, `git reset`, `git checkout`, `rm -rf`. Anything that mutates
  the tree or history should be a conscious approval.
- `cd` on its own. It is harmless in isolation, and allowlisting it would remove most remaining
  prompts — but only if the matcher genuinely checks every segment of a compound command. That is
  asserted in the settings comment, not verified, and the failure mode if it is wrong
  (`cd /tmp && rm -rf …` auto-approved) is bad enough not to gamble on.
- `time` and `timeout`. Both wrap an arbitrary command, so allowlisting either launders everything
  after it past the allowlist.

## One deliberate exception in the shared file

`Bash(python3 -c:*)` pre-approves arbitrary Python, which is not read-only. Ad-hoc analysis snippets
were the largest single source of prompts (~196 segments) and the only alternative was every user
granting it locally and diverging. Project settings apply **only while working inside this repo**, and
that scope is what makes it acceptable. The same caveat already applied to `sed`, `awk`, `echo` and
`cat`, any of which can write through a shell redirect.

`Bash(python3 - <<*)` is in the file for the same reason and does **not** work — heredocs prompt
regardless, as measured above. It stays only because removing it changes nothing.
