#!/bin/bash
# Analyze work by time windows: issue number + description
# Usage: ./scripts/commit-hours-analysis.sh [--since DATE] [--until DATE]
# Example: ./scripts/commit-hours-analysis.sh --since 2026-06-02 --until 2026-06-12

set -euo pipefail

SINCE="${SINCE:-2026-06-02}"
UNTIL="${UNTIL:-2026-06-18}"
AUTHOR="${AUTHOR:-$(git config user.name)}"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --since)
      SINCE="$2"
      shift 2
      ;;
    --until)
      UNTIL="$2"
      shift 2
      ;;
    *)
      echo "Usage: $0 [--since DATE] [--until DATE]"
      exit 1
      ;;
  esac
done

# Fetch git log and create temp file
TEMP_FILE=$(mktemp)
trap "rm -f $TEMP_FILE" EXIT

# Get commits by author OR committer (use two commands and combine)
{
  git log --all --pretty=tformat:"%ai|%s" \
    --since="$SINCE 00:00" --until="$UNTIL 23:59" \
    --author="$AUTHOR"
  git log --all --pretty=tformat:"%ai|%s" \
    --since="$SINCE 00:00" --until="$UNTIL 23:59" \
    --committer="$AUTHOR"
} | sort -u > "$TEMP_FILE"

# Parse and display results
awk -F'|' -v since="$SINCE" -v until="$UNTIL" '
BEGIN {
  print ""
  print "═══════════════════════════════════════════════════════════════════════════════════════════════"
  print "WORK BY TIME WINDOW (" since " to " until ")"
  print "═══════════════════════════════════════════════════════════════════════════════════════════════"
  print ""
}
{
  timestamp=$1
  msg=$2

  split(timestamp, parts, " ")
  date=parts[1]
  time=parts[2]
  split(time, timeParts, ":")
  hour=sprintf("%02d", timeParts[1])
  minute=sprintf("%02d", timeParts[2])

  # Convert to 30-min window: 0-29 -> :00, 30-59 -> :30
  window=(minute < 30) ? ":00" : ":30"
  timeWindow=hour window

  # Extract issue number - look for #NNNN or patterns like "fix/1234", "feat/1234"
  issue=""
  if (match(msg, /#[0-9]+/)) {
    issue=substr(msg, RSTART, RLENGTH)
  } else if (match(msg, /\/[0-9]+/)) {
    issue=substr(msg, RSTART+1, RLENGTH-1)
    issue="#" issue
  }

  # Get description - clean up commit message format
  desc=msg
  # Remove conventional commit prefix (feat/fix/refactor/etc with scope)
  gsub(/^[a-z]+(\([^)]*\))?:? */, "", desc)
  # Remove issue patterns from start
  gsub(/^(#[0-9]+|\/[0-9]+):? */, "", desc)
  # Trim
  gsub(/^ +| +$/, "", desc)

  key=date"|"timeWindow"|"issue
  timestamps[key]=timestamp
  descs[key]=desc
  dateList[date]++
}
END {
  # Collect and sort unique dates
  dateCount=0
  for (d in dateList) {
    dateCount++
    dates[dateCount]=d
  }

  # Sort dates
  for (i=1; i<=dateCount; i++) {
    for (j=i+1; j<=dateCount; j++) {
      if (dates[i] > dates[j]) {
        temp=dates[i]
        dates[i]=dates[j]
        dates[j]=temp
      }
    }
  }

  # Display for each date
  for (d=1; d<=dateCount; d++) {
    date=dates[d]

    print "📅 " date
    print "─────────────────────────────────────────────────────────────────────────────────────────────"
    printf "%-9s | %-8s | %s\n", "Time", "Issue", "Description"
    print "─────────────────────────────────────────────────────────────────────────────────────────────"

    # Collect all time windows for this date
    timeCount=0
    for (k in timestamps) {
      split(k, parts, "|")
      if (parts[1] == date) {
        timeCount++
        times[timeCount]=parts[2]
        issues[timeCount]=parts[3]
        timeKeys[timeCount]=k
      }
    }

    # Sort times
    for (i=1; i<=timeCount; i++) {
      for (j=i+1; j<=timeCount; j++) {
        if (times[i] > times[j]) {
          temp=times[i]; times[i]=times[j]; times[j]=temp
          temp=issues[i]; issues[i]=issues[j]; issues[j]=temp
          temp=timeKeys[i]; timeKeys[i]=timeKeys[j]; timeKeys[j]=temp
        }
      }
    }

    for (i=1; i<=timeCount; i++) {
      issueStr=(issues[i] == "") ? "-" : issues[i]
      desc=descs[timeKeys[i]]
      if (length(desc) > 80) {
        desc=substr(desc, 1, 77) "..."
      }
      printf "%s | %-8s | %s\n", times[i], issueStr, desc
    }

    print ""
  }
}' "$TEMP_FILE"
