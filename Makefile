# Pipeline: git log (in $REPO) -> report.md -> entries.json -> Toggl.
# Config comes from .env (see .env.example) or the environment.
-include .env
export

REPO  ?= .
SINCE ?= $(shell date -v-14d +%F)
UNTIL ?= $(shell date +%F)
OFF   ?=

.PHONY: install report busy entries run fill

install:  ## install report-to-json + toggl-fill globally (uv)
	uv tool install --force .

report:  ## generate report.md from $(REPO)'s git history
	cd $(REPO) && $(CURDIR)/commit-hours-analysis.sh --since $(SINCE) --until $(UNTIL) > $(CURDIR)/report.md

busy:  ## export existing Toggl entries so they are never double-counted
	python3 toggl_fill.py --export-busy $(SINCE) $(UNTIL) > busy.json

entries: report  ## transform report.md into entries.json (uses busy.json if present)
	python3 report_to_json.py report.md $(if $(OFF),--off $(OFF)) $(if $(wildcard busy.json),--busy busy.json) > entries.json

run: entries  ## full pipeline, dry-run preview (nothing sent)
	python3 toggl_fill.py entries.json --dry-run

fill:  ## push entries.json to Toggl (needs TOGGL_API_TOKEN)
	python3 toggl_fill.py entries.json
