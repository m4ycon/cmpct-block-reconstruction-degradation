# Compact block reconstruction degradation

Data and code behind the charts of a node whose share of compact blocks reconstructed without requesting txs fell
between Jul 18 and Oct 18, 2026. Discussion: https://bnoc.xyz/t/compact-block-reconstruction-degradation/198

Two analyses, each with its scripts in `scripts/<name>/`, its CSVs in `data/<name>/` and a notebook that only reads
those CSVs:

- **Peer groups** (`p2a_fleet_charts.ipynb`): reconstruction share, rejections and messages per day and peer group.
- **Warnet experiment** (`experiment_charts.ipynb`): the degradation reproduced in regtest, turning one actor off at
  a time to find which one causes it.

Everything in `data/peer_groups/` comes from the node's `debug.log`s, so the same charts can be drawn for any node.
`data/experiment/` comes from a warnet run.

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

<details>
<summary><b>Contents</b></summary>

- `p2a_fleet_charts.ipynb`: the charts, drawn from `data/peer_groups/`.
- `data/peer_groups/daily_metrics.csv`: compact blocks reconstructed per day, the share of them that needed no tx
  requests, and txs rejected for missing or spent inputs.
- `data/peer_groups/full_daily_msgs.csv`: messages received per day and peer group.
- `data/peer_groups/events.csv`: when each peer group arrives and stops, drawn as dashed lines.
- `scripts/peer_groups/`: the scripts that build those files from the logs, which are not in the repo.

</details>

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

## Warnet experiment

Reproduces in regtest, on [warnet](https://github.com/bitcoin-dev-project/warnet), the drop in compact blocks
reconstructed without `getblocktxn` that [bnoc #198](https://bnoc.xyz/t/compact-block-reconstruction-degradation/198)
reported while the P2A fleet was active, and tests which actor causes it by turning one off at a time.
`experiment_charts.ipynb` draws the run in the style of `p2a_fleet_charts.ipynb`.

### Usage

Needs Docker Desktop with Kubernetes, and a helm 3 binary in `.venv/bin` (the runner puts that directory first
on `PATH`). warnet 1.1.20 and the notebook dependencies go in `.venv` too. From the repo root:

```sh
uv venv && uv pip install warnet==1.1.20 pandas matplotlib nbformat nbconvert ipykernel
.venv/bin/python scripts/experiment/gen_network.py   # scripts/experiment/networks/cmpct/network.yaml
.venv/bin/python scripts/experiment/run_experiment.py --name <run>   # deploy, phases, collect, tear down (about 2h15)
.venv/bin/python scripts/experiment/parse_run.py data/experiment/<run>   # data/experiment/<run>/parsed/*.csv
```

Then point `RUN` in `experiment_charts.ipynb` at `data/experiment/<run>` and run it.
`run_experiment.py --warmup 60 --phase 90` gives a 15-minute smoke run, and `--keep` leaves the network up at the
end. Runs in `data/experiment/` other than `final` are not committed.

<details>
<summary><b>Contents</b></summary>

- `experiment_charts.ipynb`: the charts, drawn from `data/experiment/final/`.
- `data/experiment/final/`: the reported run, without its raw node logs (`debug.log` and message capture, 2.1 GB),
  so it can't be parsed again. A new run's folder also holds every tank's `debug.log` and message capture.
  - `phases.csv`: each phase's actors, start and end (heights and times).
  - `pods.csv`: every pod with its role and IPs.
  - `runner.log`, `commanders/` (gzipped), and the `network.yaml`, `node-defaults.yaml` and `params.py` it ran with.
  - `parsed/blocks.csv`: every block mined, its miner and the phase it fell in.
  - `parsed/reconstructions.csv`: one row per tank and compact block reconstruction.
  - `parsed/requested_txs.csv`: every tx a tank had to request, with the actor that sent it (from the `blocktxn`
    messages in the capture).
  - `parsed/extrapool.csv`: per tank and block, the txs that entered the extra pool and how many times that turned
    it over.
  - `parsed/messages.csv`, `parsed/connections.csv`, `parsed/rejections.csv`: P2P messages, new connections and
    rejections per minute, tank and peer group.
- `scripts/experiment/`: the three scripts above, and what they run:
  - `scenarios/`: the warnet scenarios below, and `params/__init__.py` with every rate and quantity (the scenarios'
    flags override them per run).
  - `networks/cmpct/`: the network `gen_network.py` writes, and `node-defaults.yaml` with the options of every node:
    `blockmaxweight=400000`, `blockreconstructionextratxn=20`, and `debug=net`, `cmpctblock`, `mempool`,
    `mempoolrej`.

</details>

Peer groups follow `p2a_fleet_charts.ipynb`: `p2aburn`, `fleet`, `txbroadcast`, plus `mint`. Every per-node number in
the notebook is about the innocents, the nodes no actor runs.

### How it works

`gen_network.py` writes the network: 3 miners, 30 innocents, and one node per actor. Each tank gets one or two
random `addnode` peers as a seed. warnet's Core image lets private IPs into addrman, so the tanks then find each
other and open Core's automatic outbound connections.

`run_experiment.py` keeps the miner and the background traffic on for the whole run, and starts or stops the
actors' commander pods phase by phase: 5 minutes of warmup, then seven phases of 17 minutes (17 blocks). After a
control phase with no actor, `all` (mint, fleet, sweeper) runs three times, followed each time by one actor off:
`no-sweeper`, `no-fleet`, `no-mint`. The phases live in `params.PHASES`.

The scenarios, in Bitcoin Core functional-test style, each run by warnet as a commander pod:

| scenario | what it does |
|---|---|
| `bootstrap.py` | mines past coinbase maturity and funds each actor's OP_TRUE wallet |
| `miner.py` | one block a minute on a random miner, from that miner's latest block template, fetched every `MINER_TEMPLATE_REFRESH_S` and right after each block, as pools do |
| `background.py` | wallet-like traffic sent by RPC to a random innocent; sizes, fees and cluster shapes from the prod cluster-mempool-observer on a day without the fleet |
| `mint.py` | chains of 20-24 rune mints of 125 vB that pay 17 sats each |
| `fleet.py` | P2A chains: a funded root, links of 94 vB paying 10 sats that each spend the previous anchor, and a tail that pays to CPFP the chain. Half the chains refill the anchor from the post's sample values; the other half start with an anchor large enough for every link. Shapes from the prod observer on Oct 5 |
| `sweeper.py` | watches its own node's mempool and burns one mid-chain anchor of every Fleet chain with a 66 vB tx whose fee is the anchor's whole value |

Each actor runs its own node, which gets every tx by RPC and relays it normally. Each also holds raw P2P peers that
push the same txs to a third of the tanks and rotate every few minutes. These peers send only `version`, `verack`,
`tx` and `pong`, with the post's user agents (`/Sat0shi:31.0.0/` on protocol 70015 for the Fleet, `/p2aburn:1.0/`
for the Sweeper; `/mint:0.1/` is a placeholder).

Rates are mainnet's per second (9-10 tx/s in total). Blocks come every minute with 1/10 of mainnet's weight, so
the mempool sits about as many blocks deep as on mainnet.

<details>
<summary><b>Result of the reported run</b></summary>

One continuous run (Oct 24 2026): 5 minutes of warmup, then seven phases of 17 blocks each. 30 innocent nodes
reconstructed every block, 510 reconstructions per phase. Its results are in `data/experiment/final`.

| phase | actors | reconstructed without request | txs requested per block and node | used the extra pool |
|---|---|---|---|---|
| 0. control | none | 100% | 0.00 | 0% |
| 1. all | mint, fleet, sweeper | 74% | 2.60 | 19% |
| 2. no-sweeper | mint, fleet | 99% | 0.09 | 0% |
| 3. all | mint, fleet, sweeper | 73% | 2.20 | 15% |
| 4. no-fleet | mint, sweeper | 99% | 0.54 | 2% |
| 5. all | mint, fleet, sweeper | 72% | 3.47 | 16% |
| 6. no-mint | fleet, sweeper | 83% | 1.37 | 6% |

- **The degradation needs the Fleet and the Sweeper together.** With both on, 72-74% of reconstructions need no
  request, the same in all three repetitions. Turning off either one brings it back to 99%. In both of those phases
  every requested tx was in the first block after the switch (heights 162 and 196), from chains already in the
  mempool. From the second block on, both phases are at 100%.
- **The requested txs are the Fleet's chains and the Sweeper's sweeps:** 3907 links, 647 tails, 492 refills and
  196 sweeps over the run. None came from the background traffic or the Mint. This fits the post's main
  hypothesis: a sweep spends an anchor in the middle of a chain and evicts the links after it from the nodes that
  take it, while the miner keeps the original chain in its template.
- **Turning off the Mint only partly helped** (83%, against 72-74% with it on). Blocks stayed full without it, and
  the Fleet's missing-input rejections dropped from 176-211 to 139 per node. Why is an open question: the run does
  not show the mechanism.
- **The extra pool did not drown as on mainnet.** It turned over about twice per block (median node) while the Fleet
  and Sweeper ran. Mainnet on Oct 5 is estimated at about 174 turnovers per block, assuming every rejected tx enters
  the extra pool (`blockreconstructionextratxn` = 100, 10-minute blocks). The degradation here appears at a much
  lower churn.
- Nodes differ: over the six actor phases, the share without request goes from 72% to 94% per node (median 87%).

</details>

<details>
<summary><b>Caveats</b></summary>

- One run, one seed. The three "all" phases agree, but the off phases ran once each.
- Regtest scale: a block holds about 500 txs instead of about 5000, and a fixed 60-second block interval replaces the
  random one.
- The template is always about 15 seconds old when a block is mined. The block interval is a multiple of the refresh
  interval, so blocks always land just before a refresh, where a pool's templates would be 0-15 seconds old. Without
  the refresh, almost every requested tx had been in the miner's mempool for under 5 seconds, and even the control
  phase had to request txs.
- In prod, the Mint and Fleet shapes are told apart only by size and fee: the observer stores no scripts.
- The WSL clock jumped about 6 seconds every few seconds until host time sync was left to Windows alone, before this
  run. Phases are assigned to blocks by height, so log timestamps never decide a block's phase.

</details>
