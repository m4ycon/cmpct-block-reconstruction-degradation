# Compact block reconstruction degradation

Data and code behind the charts of a node whose share of compact blocks reconstructed without requesting txs fell
between Jul 18 and Oct 18, 2026. Discussion: https://bnoc.xyz/t/compact-block-reconstruction-degradation/198

Two analyses, each with its scripts in `scripts/<name>/`, its CSVs in `data/<name>/` and a notebook that only reads
those CSVs:

- **Peer groups** (`p2a_fleet_charts.ipynb`): reconstruction share, rejections and messages per day and peer group.
- **Round trips** (`roundtrip_charts.ipynb`): on sampled days, which txs each compact block was missing and why.

Everything in `data/` comes from the node's `debug.log`s (and, for the round trips, its blocks), so the same
charts can be drawn for any node.

```sh
pip install jupyter pandas matplotlib
jupyter lab
```

## Peer groups

### Usage

To draw your own node's charts, rebuild `data/peer_groups/` from its logs. The node needs these in `bitcoin.conf`
(timestamps stay on, as by default; `-logthreadnames`, `-logsourcelocations` and `-logips` are fine):

```ini
debug=net          # messages received, getdatas sent, version messages
debug=cmpctblock   # compact block reconstructions and how many txs each one requested
debug=mempoolrej   # txs rejected, with the reason
```

`debug=net` makes the logs large. The scripts need bash, awk, grep, Python 3.11+ and the decompressor of each
rotated log (`zstd`, `gzip`, `xz` or `bzip2`). Pass the logs oldest first:

```sh
scripts/peer_groups/run_all.sh <outdir> <debug.log>...
```

For example, with a folder of rotated logs named by date (`debug.log-2026-07-09.zst`, ...) next to the live
`debug.log`, writing the per-file counts to a scratch directory and keeping only the CSVs:

```sh
scripts/peer_groups/run_all.sh /tmp/logpass ~/node-logs/debug.log-* ~/node-logs/debug.log
cp /tmp/logpass/{daily_metrics,full_daily_msgs,events}.csv data/peer_groups/
```

If the rotated names don't sort by date (logrotate's `debug.log.1`, `debug.log.2.gz`, ...), order them by
modification time instead: `$(ls -1tr ~/node-logs/debug.log*)`. `JOBS` sets how many logs are read in parallel
(default 3).

### Contents

- `p2a_fleet_charts.ipynb`: the charts, drawn from `data/peer_groups/`.
- `data/peer_groups/daily_metrics.csv`: compact blocks reconstructed per day, the share of them that needed no tx
  requests, and txs rejected for missing or spent inputs.
- `data/peer_groups/full_daily_msgs.csv`: messages received per day and peer group.
- `data/peer_groups/events.csv`: when each peer group arrives and stops, drawn as dashed lines.
- `scripts/peer_groups/`: the scripts that build those files from the logs, which are not in the repo.

The files in `data/peer_groups/` cover our node from Jul 8 to Oct 23, 2026: its logs from before Jul 8 were lost or
lack the debug categories above. Days with no logs inside a node's range are drawn empty.

Our node ran Bitcoin Core v31.0.0, and the scripts expect its log lines (see Sources); other versions or
implementations may word them differently, or not log them at all.

Peers are grouped by the user agent of their version message: `p2aburn` (`/p2aburn*`), `txbroadcast`
(`/txbroadcast*`), `fleet` (`/Sat0shi:31.0.0/`, or `/Satoshi:31.0.0/` on protocol 70015) and `other`. A group
arrives with its first version message, drawn only if the logs cover the 24 hours before it (otherwise it may
have arrived during a gap), and stops with the last message one of its peers sent, if that is more than a day
before the end of the logs.

<details>
<summary><b>Sources</b></summary>

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

</details>

<details>
<summary><b>Alternative: peer-observer</b></summary>

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

</details>

## Round trips

### Usage

To draw your own node's charts, rebuild `data/roundtrip/` from its logs and blocks. The node needs these in
`bitcoin.conf`, besides timestamps:

```ini
debug=cmpctblock   # compact block arrival, reconstruction and txs requested
debug=mempool      # txs accepted, and replaced with both txids
debug=mempoolrej   # txs rejected, with the reason and, for a replacement, both fees
debug=txpackages   # orphans stored and removed, with the orphanage size
```

The scripts need the same tools as the peer groups ones, plus `zstd` and `bitcoin-cli` talking to the node to
fetch the blocks (a pruned node works too). Pass the logs from 3 days before the first day to the end of the last
one, in any order:

```sh
scripts/roundtrip/extract.sh <workdir> <debug.log>...
python3 scripts/roundtrip/fetch_blocks.py <workdir> <YYYY-MM-DD>...
scripts/roundtrip/run_days.sh <workdir> data/roundtrip <YYYY-MM-DD>...
python3 scripts/roundtrip/example_chain.py <workdir> data/roundtrip <YYYY-MM-DD> <txid>
```

The last one writes the timeline of the chain of a link swept on that day. `JOBS` sets how many logs `extract.sh`
reads in parallel (default 3) and how many days `run_days.sh` analyzes at once (default 2); each day holds every tx
of its blocks in memory.

### Contents

- `roundtrip_charts.ipynb`: the charts, drawn from `data/roundtrip/`.
- `data/roundtrip/blocks.csv`: one row per compact block reconstructed: rebuild time (first `Saw new header` to
  "Successfully reconstructed"), txs and bytes requested and txs taken from the extra pool as logged, txs missing
  by our count and by cause, and whether that first header came in a `cmpctblock` or a `headers` message.
- `data/roundtrip/causes.csv`: missing txs and their vsize per day and cause.
- `data/roundtrip/chain_heads.csv`: the first missing tx of each P2A chain refused for its fee, by its role and by
  what our mempool held instead.
- `data/roundtrip/block_types.csv`: the txs of the day's blocks as P2A chain, 140 vB rune mint or other.
- `data/roundtrip/orphanage.csv`: orphans stored per day, how many went into a full orphanage (3,000 entries), and
  how long the missing chain txs that were orphans when their block came had been around (quartiles, minutes).
- `data/roundtrip/example_chain.csv`: one chain, step by step from the log.
- `scripts/roundtrip/`: the scripts that build those files from the logs and blocks.

The files in `data/roundtrip/` cover our node on 26 days of 2026: Jun 6, 7, 12 and 13 before the fleet; Jul 12,
13, 16, 17, 23, 24, 26, 27, Aug 8, 9, 12, 13, Sep 5, 6, Oct 5, 6, 11 and 12 while it was active (from Jul 4, when
`/txbroadcast:0.1/` first appeared, to Oct 18); and Oct 20, 21, 26 and 27 after it. Our logs have a gap from
Jun 14 to Jul 8.

Our node ran Bitcoin Core v31.0.0 without `-logthreadnames`, `-logsourcelocations` or `-logips`. The scripts match
the message text wherever it sits in the line (see Sources), but were not run on logs with those options.

A block counts on the UTC day its `cmpctblock` arrived, and is analyzed with the log lines from 00:00 UTC three
days before. One of its txs is missing if, before that `cmpctblock`, the log never shows it accepted, shows it
replaced, rejected or stored as an orphan after its last acceptance, or accepted under another wtxid. The summary
line `run_days.sh` prints for each day counts the blocks whose missing count lands between "txn requested" and
"requested + extra pool", or within 10% of "requested" (at least 2).

Each tx gets a role from its shape alone, whoever sent it (an anchor and the child that pays for it show up as a
root and a closing):

| role | inputs and outputs |
|---|---|
| root | creates a P2A output, spends none |
| link | 1 input, spending a P2A output; creates a P2A output |
| refill | several inputs, one spending a P2A output; creates a P2A output |
| closing | spends a P2A output, creates none |
| sweeper | 1 input, spending a P2A output; every output worth 0 |
| other | neither spends nor creates a P2A output |

A P2A chain tx (any role but other) takes the cause of the first missing tx above it in its chain. For one refused
for its fee, the rejection line gives the fee it lost to; its "not enough additional fees to relay" form gives the
minimum fee bump instead, so it never counts as sweeper kept.

| cause | rule |
|---|---|
| sweeper kept, chain mined | the fee it lost to is exactly the value of the anchor it spends |
| chain kept, sweeper mined | the first missing tx is a sweeper |
| root refused | the first missing tx is a root |
| other P2A | any other P2A chain tx |
| 140 vB rune mints | not P2A; 139-141 vB, 1 input, 2 outputs, a runestone (`OP_RETURN OP_13`) |
| everything else | the rest |

<details>
<summary><b>Sources</b></summary>

`extract.sh` keeps these lines (the `Saw new` ones are logged without any debug category):

```
2026-10-10T01:33:46.770727Z [mempool] AcceptToMemoryPool: peer=388692: accepted <txid> (wtxid=<wtxid>) (poolsz 75715 txn, 222516 kB)
2026-10-10T01:33:46.867764Z [mempool] replacing mempool tx <txid> (wtxid=<wtxid>, fees=448, vsize=140). New tx <txid> (wtxid=<wtxid>, fees=464, vsize=140)
2026-10-10T01:33:47.393579Z [mempoolrej] <txid> (wtxid=<wtxid>) from peer=403238 was not accepted: insufficient fee, rejecting replacement <txid>, less fees than conflicting txs; 0.0000001 < 0.0000035
2026-10-10T01:33:46.666679Z [txpackages] stored orphan tx <txid> (wtxid=<wtxid>), weight: 722 (mapsz 3001 outsz 423)
2026-10-10T01:33:46.770521Z [txpackages] removed orphan tx <txid> (wtxid=<wtxid>) (1 announcements)
2026-10-29T00:07:33.996530Z Saw new header hash=<hash> height=973615 peer=76692
2026-10-29T00:29:24.569777Z Saw new cmpctblock header hash=<hash> height=973616 peer=9200
2026-10-10T01:49:35.203144Z [cmpctblock] Initializing PartiallyDownloadedBlock for block <hash> using a cmpctblock of 23990 bytes
2026-10-26T00:13:45.290034Z [cmpctblock] Successfully reconstructed block <hash> with 1 txn prefilled, 2912 txn from mempool (incl at least 0 from extra pool) and 2 txn (370 bytes) requested
```

The txs of each block come from `getblock <hash> 3` (2 when the node has no undo data for it).

</details>
