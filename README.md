# analysis-commit-toggl

Reconstruct your Toggl Track timesheet from git commit history.

```
git log  ──►  report.md  ──►  entries.json  ──►  track.toggl.com
     (commit-hours-analysis.sh)  (report-to-json)   (toggl-fill)
```

- **`commit-hours-analysis.sh`** — turns your commits (author *or* committer, merges excluded) into a readable report of 30-minute work windows per day.
- **`report-to-json`** — fills full workdays around those windows (template 8:45–11:45 / 12:10–17:30): gaps split 65% issue work / 35% "code review", off-hours commits stay at their real time, and each ISO week is trimmed to exactly 41h20. Supports days off (`--off`) and already-tracked Toggl time (`--busy`, never double-counted). Same-window commit bursts are merged so entries stay ≥ 20 min, joining descriptions and tags.
- **`toggl-fill`** — pushes the JSON to Toggl Track: dry-run preview, skips entries that already exist (safe to re-run), handles the hourly API quota by waiting. `--export-busy SINCE UNTIL` dumps existing entries for `--busy`.

Entries get a `NNN-issue` tag (e.g. `200-1530`) and a `#1530: commit title` style description. Commits without an issue number inherit one when the same title later shows up squash-merged as `title (#1530)`; the rest fall back to the bare project tag.

## Install

Stdlib only, no dependencies. Either run the `.py` files directly with `python3`, or install the two commands globally:

```bash
uv tool install git+https://github.com/guilbep/analysis-commit-toggl
# or from a checkout:
make install
```

## Configure

Copy `.env.example` to `.env` (gitignored) and fill in:

| Variable | Meaning |
|---|---|
| `TOGGL_API_TOKEN` | from [track.toggl.com/profile](https://track.toggl.com/profile), bottom of the page |
| `TOGGL_PROJECT` | Toggl project name entries are booked on |
| `TOGGL_PROJECT_NUMBER` | prefix for the `NNN-issue` tag |
| `REPO` | path to the git repo to analyze |
| `AUTHOR` | git author to match (default: `git config user.name`) |

The Makefile loads `.env` automatically; when running scripts by hand, export the variables or prefix the command (`TOGGL_API_TOKEN=xxx toggl-fill ...`).

## Use

With the Makefile (run from this repo, pointing at any project):

```bash
make busy SINCE=2026-06-26 UNTIL=2026-07-13   # optional: fetch already-tracked time first
make run  REPO=~/works/git/my-project SINCE=2026-06-26 UNTIL=2026-07-13 OFF=2026-07-01
make fill                              # push what the dry run showed
```

`make run` regenerates `report.md` + `entries.json` and prints a dry-run preview plus per-week totals (`week 2026-W27: 33h04 (target 33h04, review 18%)`) on stderr — sanity-check those before `make fill`.

Or step by step, no Makefile:

```bash
cd ~/works/git/my-project
~/path/to/commit-hours-analysis.sh --since 2026-06-26 --until 2026-07-13 > report.md
report-to-json report.md --off 2026-07-01 > entries.json
toggl-fill entries.json --dry-run
TOGGL_API_TOKEN=xxx toggl-fill entries.json
```

## Getting issue numbers into every commit

The tagging is only as good as the `#NNNN` in your commit subjects. Name branches `type/NNNN-slug` and add a `prepare-commit-msg` git hook that appends `(#NNNN)` from the branch name — then every commit self-tags with zero discipline:

```bash
#!/bin/sh
# prepare-commit-msg: append " (#NNNN)" from branch name type/NNNN-slug
case "${2:-}" in merge|squash) exit 0 ;; esac
issue=$(git branch --show-current | sed -n 's|.*/\([0-9][0-9]*\).*|\1|p')
[ -n "$issue" ] || exit 0
head -1 "$1" | grep -qE '#[0-9]+' && exit 0
{ printf '%s (#%s)\n' "$(head -1 "$1")" "$issue"; tail -n +2 "$1"; } > "$1.tmp" && mv "$1.tmp" "$1"
```

## Notes

- Day template, week target (41h20) and review fraction (35%) are constants at the top of `report_to_json.py`.
- `report-to-json` honors the report header's date range: rebased commits keep stale author dates, so rows before the `--since` date are dropped.
- Toggl has no bulk-create endpoint and quotas API requests per hour (30 free / 240 starter / 600 premium). `toggl-fill` posts one entry per second and waits 15 min on HTTP 402/429, and re-runs only cost a few requests thanks to the duplicate check.
