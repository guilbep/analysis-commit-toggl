Three steps, in this order:

```bash
# 1. Generate the report from git history (run inside your project repo)
./commit-hours-analysis.sh --since 2026-06-26 --until 2026-07-13 > report.md

# 2. Transform the report into Toggl-ready JSON (off days optional)
python3 report-to-json.py report.md --off 2026-07-01 > entries.json

# 3. Preview, then push to Toggl
python3 toggl-fill.py entries.json --dry-run
TOGGL_API_TOKEN=xxx python3 toggl-fill.py entries.json
```

Notes:
- Step 1 must run from the git repo you're analyzing; the other two run wherever the files live.
- Check the stderr output of step 2 — it prints per-week totals and review % so you can sanity-check before pushing.
- Step 3's real run is safe to repeat: it skips entries that already exist in Toggl (same start + description).
