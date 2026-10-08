# Watchlist command reference

Moved out of `find-release-note-candidates/SKILL.md`; the per-pass rules stay there, under "Keeping
the watchlist current": `add --status` versus the short forms, `--note` becoming the summary, and
`--gate`.

The watchlist is the only memory between passes:

```
watchlist.py summary                     # per-release counts and days reviewed
watchlist.py list --status asked         # or declined, gated, noted -- implies --all
watchlist.py show <bug>                  # one entry in full: summary, gates, whole log
watchlist.py add <bug> --status asked --note "<what and when>"       # asked | declined | gated |
watchlist.py add <bug> --status declined --note "<why>"             # watching | noted | done
watchlist.py add <bug> --status watching --due 2026-09-01   # sets the "follow up after" date that
                                                            # resume and followup both display
watchlist.py decline <bug> --note "<why>"   # ONLY for a bug already on the list -- see SKILL.md
watchlist.py noted <bug> --note "<where it shipped>"   # the note is live in Nucleus
watchlist.py log "<pass summary>"        # release-level context; resume replays these
watchlist.py days 20260801               # record the day as reviewed
watchlist.py rm <bug>                    # delete the entry outright -- see the caveat below
watchlist.py add <bug> --status gated --gate <pref>   # record the gating preference; see SKILL.md
watchlist.py gates                       # re-check every recorded gate now
watchlist.py carry <bug> --from 155      # move an open entry from an older release, log intact
watchlist.py drop-release 155            # delete a finished release; refuses with open entries
```

**`rm` deletes the entry and everything recorded on it**, with no confirmation and nothing to undo:
its summary, its `--note` trail and its due date all go. The watchlist is per-user, so that history
is the only record that this bug was already judged; losing it is how a bug already declined gets
re-proposed next cycle. Use `add --status <verdict> --note "<why>"` to change a verdict, and `rm`
only for an entry created in error, such as a typo'd bug number.
