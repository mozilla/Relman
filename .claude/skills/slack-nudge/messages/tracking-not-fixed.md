# <channel>.tracking-not-fixed

For `nightly`, `beta` and `release` only (the ESR checks are different).

Bugs tracked (`+` or `blocking`) for this channel's version and not fixed
there yet. The ask: will a fix land in time for the date below?

`<version>` is the version from the check's `channel` (`Fx160 beta` → 160).

## The date

From the release schedule, never from memory:

```sh
curl -s "https://whattrainisitnow.com/api/release/schedule/?version=<channel>"
```

Check that its `version` matches `<version>`. Use the date only (the times
are UTC), written like `Thursday, November 5`.

- **nightly:** `merge_day`, when `<version>` moves to Beta.
- **beta:** the day before the last `beta_N` build (the highest N).
- **release:** the Friday before the next planned dot release: the first
  `dot_release_N` whose Friday is still ahead. None ahead: say so in the
  first prompt and leave the date for the user to give in "Other".

If that date is today or past, say so in the first prompt.

## Restricted or not

Restricted bugs (`security` true): number and link only. Public bugs: the
link followed by the summary, cut to about 80 characters.

## One bug

```
Hi @<slack name>, bug <id> is tracked for Firefox <version> and isn't fixed
yet:
<link> <summary for public bugs>
<when>. Do you think a fix will land in time? Thanks!
```

## Several bugs

```
Hi @<slack name>, these bugs are tracked for Firefox <version> and aren't
fixed yet:
• <link> <summary for public bugs>
• <link> <summary for public bugs>
<when>. Do you think fixes will land in time? Thanks!
```

## No assignee: the triage owner

Fall back to the triage owner for unassigned bugs. The message says up front
that it's for them as triage owner. `<component>` is `Product::Component`
for public bugs; for a restricted one, leave out ` for <component>`.

```
Hi @<slack name>, as triage owner for <component>: bug <id> is tracked for
Firefox <version> and has no assignee:
<link> <summary for public bugs>
<when>. Will this be assigned, and do you think a fix will land in time?
Thanks!
```

Several bugs (name each bug's component in its line instead, for public
bugs, when they differ):

```
Hi @<slack name>, as triage owner: these bugs are tracked for Firefox
<version> and have no assignee:
• <link> <summary for public bugs>
• <link> <summary for public bugs>
<when>. Will these be assigned, and do you think fixes will land in time?
Thanks!
```

## `<when>`, by channel

- **nightly:** `<version> moves to Beta on <date>`
- **beta:** `The last <version> Beta build is on <day after date>, so a fix
  would need to be uplifted by <date>`
- **release:** `The next planned <version> dot release is on <dot release
  date>, so a fix would need to be ready by <date>`
