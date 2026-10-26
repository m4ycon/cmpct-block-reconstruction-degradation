#!/bin/bash
# usage: run1.sh <outdir> <index> <logfile>
# Counts one debug.log (plain, .zst, .gz, .xz or .bz2) into <outdir>/<index>.cnt, its version messages into <index>.ver.
index=$2; log=$3; out="$1/$index"
case "$log" in
  *.zst) unpack=(zstd -dc);; *.gz) unpack=(gzip -dc);; *.xz) unpack=(xz -dc);; *.bz2) unpack=(bzip2 -dc);; *) unpack=(cat);;
esac
idle() { if command -v ionice >/dev/null; then nice -n 19 ionice -c3 "$@"; else nice -n 19 "$@"; fi; }
# a live debug.log keeps growing; read only the bytes it has now
size=$(stat -c %s "$log" 2>/dev/null || stat -f %z "$log")
idle head -c "$size" "$log" \
  | idle "${unpack[@]}" \
  | LC_ALL=C idle grep -a -F \
      -e '] received: ' -e 'sending getdata' -e 'receive version message: ' \
      -e 'Bitcoin Core version v' -e 'Bitcoin Knots version v' \
  | LC_ALL=C idle awk -v versions_out="$out.ver.tmp" -f "$(dirname "$0")/peerhour.awk" > "$out.cnt.tmp"
read -r head_rc unpack_rc grep_rc awk_rc <<< "${PIPESTATUS[*]}"
# grep exits 1 when a log has none of these lines, which is not an error
if [ "$head_rc" -ne 0 ] || [ "$unpack_rc" -ne 0 ] || [ "$grep_rc" -gt 1 ] || [ "$awk_rc" -ne 0 ]; then
  echo "$index $(basename "$log") FAILED (exit codes: $head_rc $unpack_rc $grep_rc $awk_rc)" >&2
  rm -f "$out.cnt.tmp" "$out.ver.tmp"
  exit 1
fi
# awk creates the .ver only when the log has a version message
touch "$out.ver.tmp"
mv "$out.ver.tmp" "$out.ver"
mv "$out.cnt.tmp" "$out.cnt"
echo "$index $(basename "$log") done $(date -u +%T)"
