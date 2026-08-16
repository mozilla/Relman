# Opening a pass: machine setup and the tooling check

Shared by `find-release-note-candidates` and `review-release-notes`, so the rules live in one place
and the two skills cannot drift apart. Both run before any real work — the first once per machine,
the second at the top of every pass. Neither is optional, and each has one failure mode that a pass
must relay rather than route around.

## First run on a machine: locate the Gecko clone

```
python3 scripts/relnotes/watchlist.py check-setup
```

Every script needs a Gecko checkout, and **it lives somewhere different on every machine**, so no
path can be committed to the shared tree or assumed in a skill. `check-setup` resolves it once
(`--repo` → `$RELMAN_GECKO_REPO` → saved state → `~/repos/firefox` as a legacy guess), saves it to
the per-user state file next to the watermark, and every script reads it from there.

It also reports the `git -C` permission entries the gecko reads need — one per read-only subcommand
rather than a single wildcard, which would pre-approve writes against someone's working tree. Without
them **every single gecko read prompts**, which is most of what makes a cold run feel like an
approval treadmill.

- **On a machine that has never been set up, every script exits with
  `error: could not locate the Gecko checkout` and names this command.** That error is the signal —
  don't work around it. Ask where their clone is and run `check-setup --repo <path>` once.
- If it reports a path found only as the "legacy default", it is an unsaved guess: save it with
  `check-setup --repo <path>` so the other scripts stop re-guessing.
- If it reports missing permission entries, **show them and ask before writing.** `--write` merges
  them into the git-ignored `.claude/settings.local.json`; it is granting the session standing
  approval, so it is the user's call, not yours. Never add them to the shared `settings.json`.

Writing the `git -C` calls themselves is a command-form question, not a setup one:
[`command-forms.md`](command-forms.md) has that rule and the measurement behind it.

## Every pass: the tooling check

```
python3 scripts/relnotes/watchlist.py check-updates --pull
```

**Run this first, before anything else.** Several people edit these skills and scripts, so the copy
driving your pass can be days older than the one its author is describing, and nothing else in a run
would say so. `--pull` fast-forwards this checkout when it is behind — refusing if the tree is dirty,
on a branch, or cannot fast-forward, and saying which — so the ordinary case of "someone improved a
script yesterday" fixes itself silently.

**One rule covers everything it prints: a `STOP` banner halts the pass, and every other line is
context to act on and carry into what you report.** Those lines say what they mean and need no
decoder here — a pull that did not land, a `-dirty` or `-dirty?` marker, a check that could not
run, a reference doc to re-read. **None of them is a reason to stop.** The older tooling still
works, and a run nobody else can reproduce should arrive with the reason already attached.

The banner fires when the pull touched anything under either skill's directory — today that is one
`SKILL.md` each, but the check is on the directory, so a file added beside one would count too. It is
the exception to the rule above, and it is not a judgement call:

- **Stop. Tell the user to `/clear`, then to ask for the release-note work again in the fresh
  session.** Do not continue, and do not offer to continue. A skill body is loaded into the
  conversation and stays there, so the pulled file does not replace the copy already in front of
  you — you would be running new scripts against old rules with no way to tell which of your own
  instructions were wrong.
- **`/clear` is enough. Do not tell the user to quit Claude Code.** It starts a new conversation
  with empty context and rebuilds the system prompt, so the next invocation reads the new file.
- **Only you can see any of this**, so relaying it is the whole job. `check-updates` also exits 1
  on a stale skill, so it reads as a failed command rather than a paragraph.

Drop `--pull` to ask the same question without changing anything, and add `--quiet` to print
nothing when there is nothing to act on.
