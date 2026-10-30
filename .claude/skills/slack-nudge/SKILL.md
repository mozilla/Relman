---
name: slack-nudge
description: Nudge the assignees of a dashboard check's bugs on Slack — for each assignee, find their Slack account from their Bugzilla email (or a best guess from their name), draft a message in that check's wording, and send it only after the person running the skill approves it. Bugs with the same assignee go in one message, which can be split. Use when asked things like "nudge the assignees", "slack the sec-high owners about uplift", "ping people about the pending uplift requests". Takes the check id as its argument, e.g. `/slack-nudge beta.sec-high-pending-uplift`, or offers the supported checks with their bug counts when run without one.
---

# Slack nudge

For one dashboard check, message each bug's assignee on Slack, one person at
a time. Each step shows who the message goes to and what it says, and you
decide.

## The one rule

**Never send a Slack message without an explicit go-ahead for that exact
message.** "Go-ahead" means the person running the skill picked **Send** in
the AskUserQuestion prompt showing that recipient and that text. Nothing
else counts:

- Earlier approvals never carry over to another person, another bug, or an
  edited version of the text. After any edit, ask again.
- Free text typed into "Other" is an edit instruction, never a send, even if
  it says "send". Apply it and ask again.
- No "send all", no batching, no `slack_schedule_message`, no drafts sent on
  the user's behalf.
- If anything is unclear (a guessed recipient, a failed lookup), ask; never
  send to resolve it.

`.claude/settings.json` also puts `slack_send_message` and
`slack_schedule_message` under `ask`, so Claude Code prompts for every send
too. That prompt is a backstop, not the go-ahead: get Send first.

## Slack first

Before anything else, check that the Slack MCP is connected: the tools
`mcp__slack__slack_search_users` and `mcp__slack__slack_send_message` must
be available, and `mcp__slack__slack_read_user_profile` (no arguments, which
reads your own profile) must succeed. If either fails, stop before running
any check and say:

> I need the Slack MCP to look people up and send messages, and it isn't
> connected. Add Mozilla's Slack MCP server (from Runlayer,
> mozilla.runlayer.com) to Claude Code under the name `slack`, run `/mcp` to
> sign in, then run me again.

The name matters: the `ask` rule in `.claude/settings.json` only covers
`mcp__slack__…` tools. Slack tools under any other server name: stop with
the same message rather than use them.

## Which check

The argument is a dashboard check id. The checks this skill has wording for
are the files in `messages/` next to this one, named `<check id>.md`, or
`<slug>.md` for wording shared by several channels' copies of a check (the
file says which channels):

- `beta.sec-high-pending-uplift`
- `<channel>.tracking-not-fixed`: `nightly`, `beta` and `release`

If the argument names one of them, use it. Otherwise (no argument, or one
with no file in `messages/`), ask with AskUserQuestion, one option per
check id the files in `messages/` cover. Run `node dashboard/bin/check.js <check id> --json` for each:
label the option with the JSON's `channel` and `title` (e.g.
`Fx160 beta: Sec-high pending uplift request`), and put its `count` in the
description ("4 bugs", "None", or the `state` if it isn't `ok`), so an empty
queue is obvious before picking it. AskUserQuestion takes four options; with
more checks than that, offer the four with the most bugs and name the rest in
the question so they can be typed into "Other". Keep each check's JSON for
the next step rather than running it again.

Read the chosen check's file before drafting anything; it holds the wording
and anything specific to that check.

## The bugs

The query lives in the dashboard (`dashboard/site/lib/catalogue.js`), never
in this skill or its message files:

```sh
node dashboard/bin/check.js <check id> --json
```

It reads the Bugzilla API key itself, from `$BUGZILLA_API_KEY` or else
`~/.config/bugzilla/api-key`. **Never read, print or copy the key.**
**Restricted bugs (security and other hidden bugs) are only returned with a
key.** Each entry in `bugs` has `id`,
`summary`, `product`, `component`, `assignee` (email), `assignee_name`,
`triage_owner` (email), `triage_owner_name`, `security` and `flags`;
`channel` is the channel label (e.g. `Fx160 beta`).

- `state` is `needs_key`: stop and say:

  > This check's bugs are restricted, so I can't see them without a
  > Bugzilla API key. Create one at
  > https://bugzilla.mozilla.org/userprefs.cgi?tab=apikey, save it to
  > `~/.config/bugzilla/api-key` with `chmod 600`, and run me again.
  > (Or export `$BUGZILLA_API_KEY` in your shell profile.)

- Any other `state` than `ok`: stop and report the `message`.
- `key_used` is false: say once, before the first bug, that restricted bugs
  aren't included without a key (`~/.config/bugzilla/api-key` or
  `$BUGZILLA_API_KEY`).
- No bugs: report "None." and stop.

Bugs assigned to `nobody@mozilla.org` aren't messaged, unless the check's
file says to fall back to the triage owner: then each goes to its
`triage_owner`, in that file's triage-owner wording. Triage-owner bugs are
grouped by triage owner, separately from bugs the same person is assigned,
so one person can get two steps: one as assignee, one as triage owner. The
prompt's question always says which ("as assignee" / "as triage owner").
Unassigned bugs with no triage owner, or with no fallback, aren't messaged;
list them in the
summary.

## Find each assignee on Slack

Group the bugs by `assignee` (or `triage_owner`, above). For each person,
use `slack_search_users` (`triage_owner_name` stands in for `assignee_name`
below):

1. **By email:** `keywords: ["<assignee email>"]`. One result whose profile
   email matches: **exact match**.
2. **By name**, if the email found nothing: `keywords` from `assignee_name`,
   with Bugzilla decorations removed (`[:nick]`, `(PTO until …)`, `:ni me`,
   etc.). One plausible result: **best guess**. Several: **best guess** with
   the likeliest, and the others named in the prompt.
3. Nothing either way: **not found**. Show the step anyway, so the user can
   give a Slack name or user id in "Other", or skip.

A best guess is shown as one, with the candidate's Slack name, title and
email, so the user can tell whether it's the right person.

## Draft the message

Follow the check's file in `messages/`. Whatever it says, these hold:

- **Restricted bugs (`security` true): bug number and link only.** No
  summary, component, product, keywords or anything else from the bug. Slack
  isn't covered by Bugzilla's access rules, and messages are kept and
  indexed.
- Link each bug as `https://bugzilla.mozilla.org/show_bug.cgi?id=<id>`.
- Address the person with a Slack mention: `Hi @<display name>, …` in the
  preview, sent as `<@<user id>>` so Slack links it to them.

## One assignee at a time

For each assignee, ask with AskUserQuestion. The question names the person,
how they were matched, and the bug numbers, so it stands on its own (the
dialog may hide earlier text). Put the full drafted message in the **Send**
option's `preview`.

Options (AskUserQuestion allows four):

- **Send**: send this message to this person.
- **Edit**: ask, in plain text, what to change (the wording, or the
  recipient). Apply it, then ask again with the new text in the preview.
- **Skip**: send nothing to them; move on.
- **Split**, when they have more than one bug: replace this step with one
  step per bug, each with that bug's single-bug message, so some can be
  skipped.
- **Stop**, when they have one bug: send nothing more; go to the summary.

The question's text says that typing "stop" in "Other" always stops. Any
other text in "Other" is an edit: apply it and ask again.

On **Send**: `slack_send_message` with `channel_id` set to the person's Slack
user id and the message exactly as previewed, except that `@<display name>`
becomes `<@<user id>>`. Report the message link, then
move to the next assignee. A send that fails: say so and why, and move on;
don't retry without asking.

## Summary

At the end (or on Stop), list:

- **Sent**: person, bugs, message link.
- **Skipped**: person, bugs.
- **Not found on Slack**: person, bugs.
- **Unassigned**: bugs.
- **Not reached**: assignees left after a Stop.
