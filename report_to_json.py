#!/usr/bin/env python3
"""Transform commit-hours report.md into Toggl-ready JSON, filling full workdays.

Weekday template 8:45-11:45 / 12:10-17:30. Gaps around commits are split 65%
issue work (attributed to the adjacent commit) / 35% "code review";
commit-less weekdays are all review; off-hours commits stay at their real
time. Each ISO week is then trimmed to 41h20 by shortening review entries,
latest first.

Usage: report-to-json report.md [--off 2026-07-01,2026-07-04] [--busy busy.json] > entries.json
Off days get no template fill, lower the week target by 8h16 each, and any
commits on them stay as real-time entries. --busy takes existing Toggl
entries (see toggl-fill --export-busy): their time slots are left untouched
and count toward the week target. Week stats print on stderr.
"""
import json
import os
import re
import sys
from datetime import date as Date, datetime, time as Time, timedelta

PROJECT_NUMBER = os.environ.get("TOGGL_PROJECT_NUMBER", "200")
REVIEW_DESC = "code review"
REVIEW_FRACTION = 0.35
DAY_START, LUNCH_START, LUNCH_END, DAY_END = 525, 705, 730, 1050  # 8:45 11:45 12:10 17:30
FUZZ = 120          # commit windows within 2h of the template join it
WINDOW = 30         # report buckets commits into 30-min windows
MIN_ENTRY = 20      # merge same-window commits so no entry is shorter than this
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


def propagate_issues(rows):
    # a commit often reappears later as its squash-merged PR "title (#1234)":
    # copy that issue number back onto identical rows that lack one
    known = {}
    for _, _, issue, desc in rows:
        if issue:
            known.setdefault(re.sub(r"\s*\(#\d+\)$", "", desc), issue)
    return [(d, t, issue or known.get(desc), desc) for d, t, issue, desc in rows]


def describe(issue, desc):
    return f"#{issue}: {desc}" if issue else desc


def tags(issue):
    return [f"{PROJECT_NUMBER}-{issue}" if issue else PROJECT_NUMBER]


def chunk_rows(span, wrows):
    # yield (dur, rows) chunks of at least MIN_ENTRY minutes each
    n = max(1, min(len(wrows), span // MIN_ENTRY))
    each = span // n
    for i in range(n):
        group = wrows[i * len(wrows) // n:(i + 1) * len(wrows) // n]
        yield (span - each * (n - 1) if i == n - 1 else each), group


def merged(group):
    desc = "; ".join(describe(issue, d) for _, _, issue, d in group)
    tg = []
    for _, _, issue, _ in group:
        for t in tags(issue):
            if t not in tg:
                tg.append(t)
    return desc, tg


def group_windows(rows):
    ws = []
    for r in rows:
        if ws and ws[-1][0] == r[1]:
            ws[-1][1].append(r)
        else:
            ws.append((r[1], [r]))
    return ws


def fill_day(day, rows, all_out, off, busy):
    rows = sorted(rows, key=lambda r: r[1])
    is_weekday = day.weekday() < 5 and day not in off
    template, standalone = [], []
    for r in rows:
        near = DAY_START - FUZZ <= r[1] < DAY_END + FUZZ
        (template if is_weekday and near else standalone).append(r)

    out = []  # day-local: clipped against busy intervals at the end

    def flush():
        for e in out:
            pieces = [(e["start"], e["start"] + e["dur"])]
            for bs, be in busy.get(day, []):
                pieces = [p for a, b in pieces
                          for p in ((a, min(b, bs)), (max(a, be), b)) if p[1] > p[0]]
            for a, b in pieces:
                all_out.append({**e, "start": a, "dur": b - a})

    for t, wrows in group_windows(standalone):  # off-hours: real time, 30-min window split
        s = t
        for dur, group in chunk_rows(WINDOW, wrows):
            desc, tg = merged(group)
            out.append({"day": day, "start": s, "dur": dur,
                        "desc": desc, "tags": tg, "review": False})
            s += dur
    if not is_weekday:
        return flush()

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

    def emit_rows(a, b, wrows):  # same-window commits share the span in >=MIN_ENTRY chunks
        for dur, group in chunk_rows(b - a, wrows):
            desc, tg = merged(group)
            emit(a, a + dur, desc, tg)
            a += dur

    length = tl(a_end)
    if not windows:
        emit(0, length, REVIEW_DESC, [PROJECT_NUMBER], review=True)
        return flush()

    prev = 0
    for wstart, wrows in windows:
        ws = max(tl(wstart), prev)
        we = max(tl(wstart + WINDOW), ws)
        r = int((ws - prev) * REVIEW_FRACTION)
        if r < MIN_ENTRY:  # too small for its own review entry: keep it issue work
            r = 0
        if r:
            emit(prev, prev + r, REVIEW_DESC, [PROJECT_NUMBER], review=True)
        if we > prev + r:
            emit_rows(prev + r, we, wrows)
        prev = we
    tail = length - prev
    if tail > 0:  # after last commit: 65% continues that issue, 35% review ends the day
        r = int(tail * REVIEW_FRACTION)
        if r < MIN_ENTRY:
            r = 0
        _, _, issue, desc = windows[-1][1][-1]
        if tail - r:
            emit(prev, length - r, describe(issue, desc), tags(issue))
        if r:
            emit(length - r, length, REVIEW_DESC, [PROJECT_NUMBER], review=True)
    flush()


def trim_weeks(entries, dmin, dmax, off, busy):
    weeks = {}
    for e in entries:
        weeks.setdefault(e["day"].isocalendar()[:2], []).append(e)
    busy_week = {}
    for day, ivs in busy.items():
        wk = day.isocalendar()[:2]
        busy_week[wk] = busy_week.get(wk, 0) + sum(b - a for a, b in ivs)
    for wk, es in sorted(weeks.items()):
        d, weekdays = dmin, 0
        while d <= dmax:
            if d.isocalendar()[:2] == wk and d.weekday() < 5 and d not in off:
                weekdays += 1
            d += timedelta(days=1)
        target = WEEK_TARGET * weekdays // 5
        over = sum(e["dur"] for e in es) + busy_week.get(wk, 0) - target
        for e in sorted(es, key=lambda e: (e["day"], e["start"]), reverse=True):
            if over <= 0:
                break
            if e["review"]:
                cut = min(over, e["dur"])
                e["dur"] -= cut
                over -= cut
        if over > 0:
            print(f"warning: week {wk} is {over} min over target with no review left to trim", file=sys.stderr)
        total = sum(e["dur"] for e in es) + busy_week.get(wk, 0)
        review = sum(e["dur"] for e in es if e["review"])
        existing = f", {busy_week[wk] // 60}h{busy_week[wk] % 60:02} already in toggl" if wk in busy_week else ""
        print(f"week {wk[0]}-W{wk[1]:02}: {total // 60}h{total % 60:02} "
              f"(target {target // 60}h{target % 60:02}, review {100 * review // total}%{existing})", file=sys.stderr)
    return [e for e in entries if e["dur"] > 0]


def main():
    args = sys.argv[1:]
    off = set()
    if "--off" in args:
        i = args.index("--off")
        off = {Date.fromisoformat(d) for d in args[i + 1].split(",")}
        del args[i:i + 2]
    busy = {}
    if "--busy" in args:
        i = args.index("--busy")
        for x in json.load(open(args[i + 1])):
            if x.get("duration", -1) < 0:  # running timer
                continue
            s = datetime.fromisoformat(x["start"].replace("Z", "+00:00")).astimezone()
            m = s.hour * 60 + s.minute
            busy.setdefault(s.date(), []).append((m, min(m + round(x["duration"] / 60), 1440)))
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit("usage: report-to-json.py report.md [--off DATE[,DATE...]] [--busy busy.json]")
    rows, since = parse_report(args[0])
    if not rows:
        sys.exit("no commit rows found in report")
    rows = propagate_issues(rows)
    by_day = {}
    for r in rows:
        by_day.setdefault(r[0], []).append(r)
    dmin, dmax = since or min(by_day), max(by_day)
    out = []
    d = dmin
    while d <= dmax:
        if d in by_day or (d.weekday() < 5 and d not in off):
            fill_day(d, by_day.get(d, []), out, off, busy)
        d += timedelta(days=1)
    entries = trim_weeks(out, dmin, dmax, off, busy)
    entries.sort(key=lambda e: (e["day"], e["start"]))
    json.dump([{
        "start": datetime.combine(e["day"], Time(e["start"] // 60, e["start"] % 60)).astimezone().isoformat(),
        "duration": e["dur"] * 60,
        "description": e["desc"],
        "tags": e["tags"],
    } for e in entries], sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
