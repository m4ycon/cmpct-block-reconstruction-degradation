#!/bin/bash
# usage: extract.sh <workdir> <debug.log>...
# Extracts each log into <workdir>/extract/ (see extract1.sh), JOBS at a time (default 3). The logs can come in
# any order: analyze_day.py orders the extracts by their first timestamp.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
[ $# -ge 2 ] || { echo "usage: $0 <workdir> <debug.log>..." >&2; exit 1; }
out=$1/extract; shift
mkdir -p "$out"
printf '%s\0' "$@" | xargs -0 -n1 -P"${JOBS:-3}" "$here/extract1.sh" "$out"
