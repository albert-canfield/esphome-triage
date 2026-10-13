# esphome-triage

Small helper for people who want to help with [ESPHome](https://github.com/esphome/esphome) issues.
It answers three questions before you write a comment or start a PR:

1. **Has this been reported before?** Finds open and closed issues and PRs that look like the same
   problem, and says why (same component, same log line, same crash frame).
2. **Which change broke it?** For "worked in A, broken in B" reports, lists the PRs that touched the
   affected components between the two releases. Usually that's a handful instead of hundreds.
3. **Is someone already on it?** Lists open PRs that touch the same component, so we don't duplicate
   work or step on someone else's PR.

It only reads public data with the GitHub CLI and git. It never posts anything.

## Setup

Needs Python 3.12+, `git`, and the [GitHub CLI](https://cli.github.com/) logged in (`gh auth login`).
No Python packages to install.

```sh
python3 -m esphome_triage refresh            # issues + PRs, plus a history-only clone of esphome
```

The first refresh takes a few minutes. The history clone has commits and file lists only (no file
contents), so it stays small.

## Use

```sh
python3 -m esphome_triage related 19257      # similar issues and PRs, with reasons
python3 -m esphome_triage narrow 19257       # PRs between the working and broken release
python3 -m esphome_triage narrow --good 2026.4.5 --bad 2026.5.0 -c es8388 -c i2s_audio
python3 -m esphome_triage overlap -c lvgl    # open PRs on a component, before you start work
python3 -m esphome_triage report             # cross-link every open issue into triage-report.md
```

`narrow` guesses versions and components from the report. Override with `--good`, `--bad` and `-c`
when it guesses wrong, add `--with-config` to also watch the components in the reporter's YAML, and
`--core` to include `esphome/core/`.

## Please be kind with the output

Scores are hints, not proof. Read both issues before saying they are the same, and keep comments short.
Maintainers are volunteers: one useful comment helps; ten "maybe related?" comments don't.
If something looks like a security problem, don't post it anywhere public. Report it privately as
described in ESPHome's [security policy](https://github.com/esphome/.github/blob/main/SECURITY.md).

## Tests

```sh
python3 -m unittest discover -s tests -t .
```
