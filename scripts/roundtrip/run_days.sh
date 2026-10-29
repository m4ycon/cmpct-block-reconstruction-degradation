#!/bin/bash
# usage: run_days.sh <workdir> <dest_dir> <YYYY-MM-DD>...
# Runs analyze_day.py for each day, JOBS at a time (default 2), then collect.py into <dest_dir>. Needs
# extract.sh and fetch_blocks.py to have filled <workdir>. Each day's summary line goes to stdout.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 3 ] || { echo "usage: $0 <workdir> <dest_dir> <YYYY-MM-DD>..." >&2; exit 1; }
workdir=$1; dest=$2; shift 2
printf '%s\0' "$@" | xargs -0 -n1 -P"${JOBS:-2}" python3 -I "$here/analyze_day.py" "$workdir"
python3 -I "$here/collect.py" "$workdir" "$dest" "$@"
