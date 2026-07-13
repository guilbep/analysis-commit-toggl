#!/usr/bin/env python3
"""Transform commit-hours report.md into Toggl-ready JSON, filling full workdays.

Weekday template 8:45-11:45 / 12:10-17:30. Gaps around commits are split 65%
issue work (attributed to the adjacent commit) / 35% "code review";
commit-less weekdays are all review; off-hours commits stay at their real
time. Each ISO week is then trimmed to 41h20 by shortening review entries,
latest first.

Usage: ./report-to-json.py report.md [--off 2026-07-01,2026-07-04] > entries.json
Off days get no template fill, lower the week target by 8h16 each, and any
commits on them stay as real-time entries. Week stats print on stderr.
"""
import json
import re
import sys
from datetime import date as Date, datetime, time as Time, timedelta

PROJECT_NUMBER = "200"
REVIEW_DESC = "code review"
REVIEW_FRACTION = 0.35
DAY_START, LUNCH_START, LUNCH_END, DAY_END = 525, 705, 730, 1050  # 8:45 11:45 12:10 17:30
FUZZ = 120          # commit windows within 2h of the template join it
WINDOW = 30         # report buckets commits into 30-min windows
WEEK_TARGET = 2480  # 41h20 per full 5-day week (496 min per weekday)

RANGE_RE = re.compile(r"WORK BY TIME WINDOW \((\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})\)")
DATE_RE = re.compile(r"📅 (\d{4}-\d{2}-\d{2})")
ROW_RE = re.compile(r"^(\d{2}):(\d{2}) \| (\S+)\s*\| (.+)$")


def parse_report(path):
    rows, day, since = [], None, None
    for line in open(path, encoding="utf-8"):
        m = RANGE_RE.search(line)
        if m:
            since = Date.fromisoformat(m.group(1))
            continue
        m = DATE_RE.search(line)
        if m:
            day = Date.fromisoformat(m.group(1))
            continue
        m = ROW_RE.match(line.strip())
        if m and day and not (since and day < since):
            # rebased commits keep stale author dates predating the report range
            h, mn, issue, desc = m.groups()
            rows.append((day, int(h) * 60 + int(mn), None if issue == "-" else issue.lstrip("#"), desc.strip()))
    return rows, since


def describe(issue, desc):
    return f"#{issue}: {desc}" if issue else desc


def tags(issue):
    return [f"{PROJECT_NUMBER}-{issue}" if issue else PROJECT_NUMBER]


def group_windows(rows):
    ws = []
    for r in rows:
        if ws and ws[-1][0] == r[1]:
            ws[-1][1].append(r)
        else:
            ws.append((r[1], [r]))
    return ws


def fill_day(day, rows, out, off):
    rows = sorted(rows, key=lambda r: r[1])
    is_weekday = day.weekday() < 5 and day not in off
    template, standalone = [], []
    for r in rows:
        near = DAY_START - FUZZ <= r[1] < DAY_END + FUZZ
        (template if is_weekday and near else standalone).append(r)

    for t, wrows in group_windows(standalone):  # off-hours: real time, 30-min window split
        each = WINDOW // len(wrows)
        s = t
        for i, (_, _, issue, desc) in enumerate(wrows):
            dur = WINDOW - each * (len(wrows) - 1) if i == len(wrows) - 1 else each
            out.append({"day": day, "start": s, "dur": dur,
                        "desc": describe(issue, desc), "tags": tags(issue), "review": False})
            s += dur
    if not is_weekday:
        return

    windows = group_windows(template)
    m_start = min(DAY_START, windows[0][0]) if windows else DAY_START
    a_end = max(DAY_END, windows[-1][0] + WINDOW) if windows else DAY_END
    morning = LUNCH_START - m_start

    def tl(t):  # wall minute -> timeline offset (lunch removed)
        t = min(max(t, m_start), a_end)
        if t <= LUNCH_START:
            return t - m_start
        return morning + max(t, LUNCH_END) - LUNCH_END

    def untl(o):  # start-of-entry mapping: offset == morning means "after lunch"
        return m_start + o if o < morning else LUNCH_END + o - morning

    def emit(a, b, desc, tg, review=False):
        for x, y in ((a, min(b, morning)), (max(a, morning), b)):  # split at lunch
            if y > x:
                out.append({"day": day, "start": untl(x), "dur": y - x,
                            "desc": desc, "tags": tg, "review": review})

    def emit_rows(a, b, wrows):  # same-window commits share the span sequentially
        each = (b - a) // len(wrows)
        for i, (_, _, issue, desc) in enumerate(wrows):
            end = b if i == len(wrows) - 1 else a + each
            emit(a, end, describe(issue, desc), tags(issue))
            a = end

    length = tl(a_end)
    if not windows:
        emit(0, length, REVIEW_DESC, [PROJECT_NUMBER], review=True)
        return

    prev = 0
    for wstart, wrows in windows:
        ws = max(tl(wstart), prev)
        we = max(tl(wstart + WINDOW), ws)
        r = int((ws - prev) * REVIEW_FRACTION)
        if r:
            emit(prev, prev + r, REVIEW_DESC, [PROJECT_NUMBER], review=True)
        if we > prev + r:
            emit_rows(prev + r, we, wrows)
        prev = we
    tail = length - prev
    if tail > 0:  # after last commit: 65% continues that issue, 35% review ends the day
        r = int(tail * REVIEW_FRACTION)
        _, _, issue, desc = windows[-1][1][-1]
        if tail - r:
            emit(prev, length - r, describe(issue, desc), tags(issue))
        if r:
            emit(length - r, length, REVIEW_DESC, [PROJECT_NUMBER], review=True)


def trim_weeks(entries, dmin, dmax, off):
    weeks = {}
    for e in entries:
        weeks.setdefault(e["day"].isocalendar()[:2], []).append(e)
    for wk, es in sorted(weeks.items()):
        d, weekdays = dmin, 0
        while d <= dmax:
            if d.isocalendar()[:2] == wk and d.weekday() < 5 and d not in off:
                weekdays += 1
            d += timedelta(days=1)
        target = WEEK_TARGET * weekdays // 5
        over = sum(e["dur"] for e in es) - target
        for e in sorted(es, key=lambda e: (e["day"], e["start"]), reverse=True):
            if over <= 0:
                break
            if e["review"]:
                cut = min(over, e["dur"])
                e["dur"] -= cut
                over -= cut
        if over > 0:
            print(f"warning: week {wk} is {over} min over target with no review left to trim", file=sys.stderr)
        total = sum(e["dur"] for e in es)
        review = sum(e["dur"] for e in es if e["review"])
        print(f"week {wk[0]}-W{wk[1]:02}: {total // 60}h{total % 60:02} "
              f"(target {target // 60}h{target % 60:02}, review {100 * review // total}%)", file=sys.stderr)
    return [e for e in entries if e["dur"] > 0]


if __name__ == "__main__":
    args = sys.argv[1:]
    off = set()
    if "--off" in args:
        i = args.index("--off")
        off = {Date.fromisoformat(d) for d in args[i + 1].split(",")}
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit("usage: report-to-json.py report.md [--off DATE[,DATE...]]")
    rows, since = parse_report(args[0])
    by_day = {}
    for r in rows:
        by_day.setdefault(r[0], []).append(r)
    dmin, dmax = since or min(by_day), max(by_day)
    out = []
    d = dmin
    while d <= dmax:
        if d in by_day or (d.weekday() < 5 and d not in off):
            fill_day(d, by_day.get(d, []), out, off)
        d += timedelta(days=1)
    entries = trim_weeks(out, dmin, dmax, off)
    entries.sort(key=lambda e: (e["day"], e["start"]))
    json.dump([{
        "start": datetime.combine(e["day"], Time(e["start"] // 60, e["start"] % 60)).astimezone().isoformat(),
        "duration": e["dur"] * 60,
        "description": e["desc"],
        "tags": e["tags"],
    } for e in entries], sys.stdout, indent=2, ensure_ascii=False)
    print()
