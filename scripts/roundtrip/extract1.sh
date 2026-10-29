#!/bin/bash
# usage: extract1.sh <outdir> <logfile>
# Keeps the lines of one debug.log (plain, .zst, .gz, .xz or .bz2) that analyze_day.py reads, in
# <outdir>/<name>.grep.zst, and the timestamps of the first and last of them in <outdir>/<name>.range.
log=$2; name=$(basename "$log"); name=${name%.zst}; name=${name%.gz}; name=${name%.xz}; name=${name%.bz2}
out="$1/$name"
case "$log" in
  *.zst) unpack=(zstd -dcf);; *.gz) unpack=(gzip -dc);; *.xz) unpack=(xz -dc);; *.bz2) unpack=(bzip2 -dc);; *) unpack=(cat);;
esac
idle() { if command -v ionice >/dev/null; then nice -n 19 ionice -c3 "$@"; else nice -n 19 "$@"; fi; }
# a live debug.log keeps growing; read only the bytes it has now
size=$(stat -c %s "$log" 2>/dev/null || stat -f %z "$log")
idle head -c "$size" "$log" \
  | idle "${unpack[@]}" \
  | LC_ALL=C idle grep -a -F \
      -e ': accepted' -e 'replacing mempool tx' -e 'was not accepted' \
      -e 'stored orphan tx' -e 'removed orphan tx' \
      -e 'Saw new' -e 'Initializing PartiallyDownloadedBlock' -e 'reconstruct' \
  | LC_ALL=C idle awk -v range_out="$out.range.tmp" \
      'NR == 1 { first = $1 } { last = $1; print } END { print first "\t" last > range_out }' \
  | idle zstd -q -o "$out.grep.zst.tmp"
read -r head_rc unpack_rc grep_rc awk_rc zstd_rc <<< "${PIPESTATUS[*]}"
# grep exits 1 when a log has none of these lines, which is not an error
if [ "$head_rc" -ne 0 ] || [ "$unpack_rc" -ne 0 ] || [ "$grep_rc" -gt 1 ] || [ "$awk_rc" -ne 0 ] || [ "$zstd_rc" -ne 0 ]; then
  echo "$name FAILED (exit codes: $head_rc $unpack_rc $grep_rc $awk_rc $zstd_rc)" >&2
  rm -f "$out.grep.zst.tmp" "$out.range.tmp"
  exit 1
fi
mv "$out.range.tmp" "$out.range"
mv "$out.grep.zst.tmp" "$out.grep.zst"
echo "$name done $(date -u +%T)"
