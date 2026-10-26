# Counts messages per (segment, hour, peer, message type), and copies each version-message line, prefixed with its
# segment, to the file named by -v versions_out. A segment starts at each bitcoind start line, since peer ids
# restart.
# Fields are found by name rather than position, so lines logged with -logthreadnames, -logsourcelocations or
# -logips parse the same way.
# Output lines: segment, hour (YYYY-MM-DDTHH), peer=<id>, message type, count, bytes. Sent getdatas are counted
# as type "getdata_sent", with no bytes.

function hour() { return substr($1, 1, 13) }

# -logips puts a comma after the peer field
function peer_field(   i) {
  for (i = NF; i > 1; i--)
    if ($i ~ /^peer=[0-9]+,?$/) { sub(/,$/, "", $i); return $i }
  return "peer=?"
}

function field_index(name,   i) {
  for (i = 2; i < NF && $i != name; i++);
  return i
}

BEGIN { segment = 0; OFS = "\t" }

# ... received: tx (192 bytes) peer=788
/\] received: / {
  i = field_index("received:")
  key = segment SUBSEP hour() SUBSEP peer_field() SUBSEP $(i + 1)
  count[key]++
  bytes[key] += substr($(i + 2), 2)
  next
}

/ sending getdata \(/ { count[segment SUBSEP hour() SUBSEP peer_field() SUBSEP "getdata_sent"]++; next }

/Bitcoin (Core|Knots) version v/ { segment++; next }

/receive version message: / { print segment, $0 > versions_out }

END {
  for (key in count) {
    split(key, part, SUBSEP)
    print part[1], part[2], part[3], part[4], count[key], bytes[key]
  }
}
