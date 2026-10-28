# Reads one debug.log and writes:
# - stdout: messages per (segment, hour, peer, message type). Lines: segment, hour (YYYY-MM-DDTHH), peer=<id>,
#   message type, count, bytes. Sent getdatas are counted as type "getdata_sent", with no bytes.
# - versions_out: each version-message line, prefixed with its segment.
# - daily_out: per-day metrics. Lines: day, metric, count. Metrics: blocks_reconstructed,
#   reconstructed_without_request (no tx had to be requested), rejected_inputs_missing.
# - seen_out: when each peer last sent us a message. Lines: segment, peer=<id>, timestamp.
# A segment starts at each bitcoind start line, since peer ids restart.
# Fields are found by name rather than position, so lines logged with -logthreadnames, -logsourcelocations or
# -logips parse the same way.

function day() { return substr($1, 1, 10) }

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
  peer = peer_field()
  key = segment SUBSEP hour() SUBSEP peer SUBSEP $(i + 1)
  count[key]++
  bytes[key] += substr($(i + 2), 2)
  last_seen[segment SUBSEP peer] = $1
  next
}

/ sending getdata \(/ { count[segment SUBSEP hour() SUBSEP peer_field() SUBSEP "getdata_sent"]++; next }

# ... Successfully reconstructed block <hash> with 1 txn prefilled, ... from extra pool) and 3 txn (900 bytes) requested
/ Successfully reconstructed block / {
  requested = $0
  sub(/.* and /, "", requested)
  sub(/ .*/, "", requested)
  metric[day() SUBSEP "blocks_reconstructed"]++
  if (requested + 0 == 0)
    metric[day() SUBSEP "reconstructed_without_request"]++
  next
}

# ... from peer=281 was not accepted: bad-txns-inputs-missingorspent
# The reason may be followed by ", <details>".
/ was not accepted: / {
  reason = $0
  sub(/.* was not accepted: /, "", reason)
  sub(/,.*/, "", reason)
  if (reason == "bad-txns-inputs-missingorspent")
    metric[day() SUBSEP "rejected_inputs_missing"]++
  next
}

/Bitcoin (Core|Knots) version v/ { segment++; next }

/receive version message: / { print segment, $0 > versions_out }

END {
  for (key in count) {
    split(key, part, SUBSEP)
    print part[1], part[2], part[3], part[4], count[key], bytes[key]
  }
  for (key in metric) {
    split(key, part, SUBSEP)
    print part[1], part[2], metric[key] > daily_out
  }
  for (key in last_seen) {
    split(key, part, SUBSEP)
    print part[1], part[2], last_seen[key] > seen_out
  }
}
