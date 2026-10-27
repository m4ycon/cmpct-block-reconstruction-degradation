# Compact block reconstruction degradation

Data and code behind the charts of a node whose share of compact blocks reconstructed without requesting txs fell
between Jul 18 and Oct 18, 2026. Discussion: https://bnoc.xyz/t/compact-block-reconstruction-degradation/198

Everything in `data/` comes from the node's `debug.log`s, so the same charts can be drawn for any node.

## Usage

```sh
pip install jupyter pandas matplotlib
jupyter lab p2a_fleet_charts.ipynb
```

To draw your own node's charts, rebuild `data/` from its logs. The node needs these in `bitcoin.conf` (timestamps
stay on, as by default; `-logthreadnames`, `-logsourcelocations` and `-logips` are fine):

```ini
debug=net          # messages received, getdatas sent, version messages
debug=cmpctblock   # compact block reconstructions and how many txs each one requested
debug=mempoolrej   # txs rejected, with the reason
```

`debug=net` makes the logs large. The scripts need bash, awk, grep, Python 3.11+ and the decompressor of each
rotated log (`zstd`, `gzip`, `xz` or `bzip2`). Pass the logs oldest first:

```sh
scripts/run_all.sh <outdir> <debug.log>...
```

For example, with a folder of rotated logs named by date (`debug.log-2026-07-09.zst`, ...) next to the live
`debug.log`, writing the per-file counts to a scratch directory and keeping only the CSVs:

```sh
scripts/run_all.sh /tmp/logpass ~/node-logs/debug.log-* ~/node-logs/debug.log
cp /tmp/logpass/{daily_metrics,full_daily_msgs,events}.csv data/
```

If the rotated names don't sort by date (logrotate's `debug.log.1`, `debug.log.2.gz`, ...), order them by
modification time instead: `$(ls -1tr ~/node-logs/debug.log*)`. `JOBS` sets how many logs are read in parallel
(default 3).

## Contents

- `p2a_fleet_charts.ipynb`: the charts, drawn from `data/`.
- `data/daily_metrics.csv`: compact blocks reconstructed per day, the share of them that needed no tx requests,
  and txs rejected for missing or spent inputs.
- `data/full_daily_msgs.csv`: messages received per day and peer group.
- `data/events.csv`: when each peer group arrives and stops, drawn as dashed lines.
- `scripts/`: the scripts that build those files from the logs, which are not in the repo.

The files in `data/` cover our node from Jul 8 to Oct 23, 2026: its logs from before Jul 8 were lost or lack the
debug categories above. Days with no logs inside a node's range are drawn empty.

Our node ran Bitcoin Core v31.0.0, and the scripts expect its log lines (see Sources); other versions or
implementations may word them differently, or not log them at all.

Peers are grouped by the user agent of their version message: `p2aburn` (`/p2aburn*`), `txbroadcast`
(`/txbroadcast*`), `fleet` (`/Sat0shi:31.0.0/`, or `/Satoshi:31.0.0/` on protocol 70015) and `other`. A group
arrives with its first version message, drawn only if the logs cover the 24 hours before it (otherwise it may
have arrived during a gap), and stops with the last message one of its peers sent, if that is more than a day
before the end of the logs.

## Sources

`full_daily_msgs.csv` and `events.csv` come from these `debug=net` lines (plus the startup line, since peer ids
restart with bitcoind):

```
2026-10-23T23:39:10.631857Z [net] received: tx (192 bytes) peer=788
2026-10-23T23:34:41.473523Z [net] sending getdata (37 bytes) peer=72908
2026-08-04T02:35:00.562134Z [net] receive version message: /Sat0shi:31.0.0/: version 70015, blocks=0, us=<addr>:8333, txrelay=1, peer=118652
2026-10-18T15:24:15.890582Z Bitcoin Core version v31.0.0 (release build)
```

`daily_metrics.csv` comes from these `debug=cmpctblock` and `debug=mempoolrej` lines, counted on the UTC day of
their timestamp; a block counts as reconstructed without requesting txs when it requested 0:

```
2026-10-26T00:13:45.290034Z [cmpctblock] Successfully reconstructed block <hash> with 1 txn prefilled, 2912 txn from mempool (incl at least 0 from extra pool) and 2 txn (370 bytes) requested
2026-10-26T00:15:04.249431Z [mempoolrej] <txid> (wtxid=<wtxid>) from peer=281 was not accepted: bad-txns-inputs-missingorspent
```

## Alternative: peer-observer

Nothing in this repo uses [peer-observer](https://github.com/peer-observer/peer-observer). If your node runs it,
the `daily_metrics.csv` numbers can come from its Prometheus instead of the logs (the charts first posted in the
discussion were drawn that way). Export one value per UTC day (`increase(...[1d])` at each midnight covers the day
before it) into these columns:

```promql
# reconstruction_no_request_share (log-extractor, needs debug=cmpctblock)
sum(increase(peerobserver_log_compact_block_reconstruction_count{tx_requested="false"}[1d]))
  / sum(increase(peerobserver_log_compact_block_reconstruction_count[1d]))

# blocks_reconstructed
sum(increase(peerobserver_log_compact_block_reconstruction_count[1d]))

# rejected_inputs_missing (ebpf-extractor, mempool:rejected tracepoint)
sum(increase(peerobserver_mempool_rejected{reason="bad-txns-inputs-missingorspent"}[1d]))
```

The log-extractor counts a reconstruction only when it also saw the block's `Saw new header` line, and the
rejections come from the `mempool:rejected` tracepoint rather than the `mempoolrej` log line, so the numbers can
differ slightly from the log pass. When we compared both on our node, Jul 8 to Oct 23: the same blocks
reconstructed on 105 of 108 days (one more in the logs on the others), and a median of 2% more rejections in the
logs, more on a few days (twice as many on Sep 12, when bitcoind restarted). `full_daily_msgs.csv` and
`events.csv` come from the logs either way.
