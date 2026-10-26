# Compact block reconstruction degradation

Data and code behind the charts of a peer-observer node whose share of compact blocks reconstructed without
requesting txs fell between Jul 18 and Oct 18, 2026. Discussion: https://bnoc.xyz/t/compact-block-reconstruction-degradation/198

## Usage

```sh
pip install jupyter pandas matplotlib
jupyter lab p2a_fleet_charts.ipynb
```

To rebuild `full_daily_msgs.csv` from a node's logs (needs `debug=net`), pass them oldest first:

```sh
scripts/run_all.sh <outdir> <debug.log>...
```

For example, with a folder of rotated logs named by date (`debug.log-2026-07-09.zst`, ...) next to the live
`debug.log`, writing the per-file counts to a scratch directory and keeping only the CSV:

```sh
scripts/run_all.sh /tmp/logpass ~/node-logs/debug.log-* ~/node-logs/debug.log
cp /tmp/logpass/full_daily_msgs.csv data/
```

If the rotated names don't sort by date (logrotate's `debug.log.1`, `debug.log.2.gz`, ...), order them by
modification time instead: `$(ls -1tr ~/node-logs/debug.log*)`.

## Contents

- `p2a_fleet_charts.ipynb`: the charts, drawn from `data/`.
- `data/daily_metrics.csv`: daily peer-observer metrics from Prometheus (May 4 to Oct 23).
- `data/full_daily_msgs.csv`: messages received per day and peer group, from the node's debug.logs (Jul 8 to Oct 23).
- `scripts/`: the scripts that built `full_daily_msgs.csv` from the logs, which are not in the repo.

Peers are grouped by the user agent of their version message: `p2aburn` (`/p2aburn*`), `txbroadcast`
(`/txbroadcast*`), `fleet` (`/Sat0shi:31.0.0/`, or `/Satoshi:31.0.0/` on protocol 70015) and `other`.

## Sources

`full_daily_msgs.csv` comes from these `debug=net` lines (plus the startup line, since peer ids restart with
bitcoind):

```
2026-10-23T23:39:10.631857Z [net] received: tx (192 bytes) peer=788
2026-10-23T23:34:41.473523Z [net] sending getdata (37 bytes) peer=72908
2026-08-04T02:35:00.562134Z [net] receive version message: /Sat0shi:31.0.0/: version 70015, blocks=0, us=<addr>:8333, txrelay=1, peer=118652
2026-10-18T15:24:15.890582Z Bitcoin Core version v31.0.0 (release build)
```

`daily_metrics.csv` comes from peer-observer's Prometheus metrics, one value per UTC day:

```promql
# blocks reconstructed without requesting txs (log-extractor, needs debug=cmpctblock)
sum(increase(peerobserver_log_compact_block_reconstruction_count{tx_requested="false"}[1d]))
  / sum(increase(peerobserver_log_compact_block_reconstruction_count[1d]))

# txs rejected for missing or spent inputs (ebpf-extractor, mempool:rejected tracepoint)
sum(increase(peerobserver_mempool_rejected{reason="bad-txns-inputs-missingorspent"}[1d]))

# connections by user agent (ebpf-extractor, inbound version messages)
sum(increase(peerobserver_p2p_version_useragent{useragent=~"/p2aburn.*"}[1d]))
sum(increase(peerobserver_p2p_version_useragent{useragent="/Sat0shi:31.0.0/"}[1d]))
```
