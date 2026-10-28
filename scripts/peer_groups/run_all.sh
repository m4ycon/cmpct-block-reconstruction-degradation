#!/bin/bash
# usage: run_all.sh <outdir> <debug.log>...
# Give the logs oldest first: each peer id's user agent is carried from one file to the next in that order.
# JOBS sets how many logs are read in parallel (default 3).
# Writes <outdir>/full_daily_msgs.csv and <outdir>/full_hourly_tx.csv. Along the way: the counts of each log in
# <outdir>/out/<index>.cnt and .ver (jobs.txt maps each index to its log), the joined counts in <outdir>/agg/,
# and join.py's progress and user-agent tally in <outdir>/join.log.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 2 ] || { echo "usage: $0 <outdir> <debug.log>..." >&2; exit 1; }
out=$1; shift
mkdir -p "$out/out" "$out/agg"

# -1 for a log that is gone
sizes() { for log in "$@"; do stat -c %s "$log" 2>/dev/null || stat -f %z "$log" 2>/dev/null || echo -1; done; }

jobs=()  # index, log, index, log, ...
i=0
for log in "$@"; do jobs+=("$(printf '%05d' "$i")" "$log"); i=$((i + 1)); done
printf '%s\t%s\n' "${jobs[@]}" > "$out/jobs.txt"
sizes_before=$(sizes "$@")
printf '%s\0' "${jobs[@]}" | xargs -0 -n2 -P"${JOBS:-3}" "$here/run1.sh" "$out/out"

# logrotate's copytruncate empties the live log; if that happened mid-run, its old lines are in a log not given here
paste <(echo "$sizes_before") <(sizes "$@") <(printf '%s\n' "$@") \
  | awk -F'\t' '
      { before = $1; after = $2; file = $3 }
      after >= 0 && after < before {
        print file " shrank during the run (rotated?); run again with its rotated copy" > "/dev/stderr"
        shrank = 1
      }
      END { exit shrank }'

python3 -I "$here/join.py" "$out/out" "$out/agg" 2> "$out/join.log"
python3 -I "$here/daily_csv.py" "$out/agg" "$out"
