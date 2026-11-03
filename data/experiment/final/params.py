"""Pace and quantities of the experiment, in one place. Scenario flags override these per run.

Per-node limits that scale with the block interval (block weight, extra pool size) are bitcoind
options and live in networks/cmpct/node-defaults.yaml.

This is a package's __init__.py because `warnet run` only bundles paths containing __init__.py,
commander.py, test_framework, ln_framework or the scenario's own file name.

Traffic shapes come from one day of confirmed txs in the prod cluster-mempool-observer. Rates are
mainnet's per second, which the 1/10 block weight at 1/10 the block interval keeps in proportion.
"""

from test_framework.messages import NODE_NETWORK, NODE_NONE, NODE_WITNESS

BLOCK_INTERVAL_S = 60
# Pools mine a template built a little earlier, not the mempool of the instant the block is found, so
# the txs that arrived since are left for the next block. The miner refreshes templates this often
# and right after every block.
MINER_TEMPLATE_REFRESH_S = 15

# Funding: UTXOs per actor and their value. Chains only spend funded UTXOs at the root and the tail.
FUNDING_UTXOS = {
    "background": 30000,
    "mint": 3000,
    "fleet": 5000,
}
# Covers the fee of the largest, highest-feerate background txs, a few links deep.
FUNDING_UTXO_SATS = 1_000_000

# Background: wallet users sending through their own node (RPC on a random innocent).
BACKGROUND_TX_PER_S = 3.1
# Txs per cluster -> weight. Clusters are chains, each link spending the previous one's output.
BACKGROUND_CLUSTER_SIZES = {1: 214586, 2: 14274, 3: 3031, 4: 1040, 5: 606, 6: 395, 7: 307, 8: 302, 9: 195, 10: 147}
BACKGROUND_LINK_GAP_S = 100
# Inverse CDFs as (quantile, value) points, sampled with linear interpolation. The 0 and 1 ends are
# not observed: 104 vB is the smallest MiniWallet tx, and the top of the vsize tail keeps the mean.
BACKGROUND_VSIZE = [
    (0, 104), (0.05, 109), (0.1, 110), (0.15, 113), (0.2, 137), (0.25, 140), (0.4, 141), (0.45, 141),
    (0.5, 143), (0.55, 152), (0.6, 164), (0.65, 180), (0.7, 204), (0.75, 210), (0.8, 226), (0.85, 276),
    (0.9, 362), (0.95, 584), (0.99, 3133), (1, 15000),
]
BACKGROUND_FEERATE = [  # sat/vB
    (0, 0.1), (0.05, 0.51), (0.1, 1.0), (0.2, 1.01), (0.25, 1.11), (0.3, 1.21), (0.35, 1.57), (0.4, 2.0),
    (0.5, 2.06), (0.55, 2.28), (0.6, 2.35), (0.65, 3.01), (0.7, 3.02), (0.75, 3.32), (0.8, 3.84),
    (0.85, 4.07), (0.9, 5.02), (0.95, 6.9), (0.99, 20), (1, 50),
]

# Actors (Mint, Fleet, Sweeper) each run their own node, which gets every tx over RPC and relays it
# as usual, plus raw P2P peers that push the same txs to other tanks: how many tanks each holds at
# once and how long a connection lasts before the actor drops it for another random tank.
ACTOR_MAX_PEERS = 12
ACTOR_CONN_LIFETIME_S = 240

# Mint: rune mint chains without P2A.
MINT_NODE = "mint-0"
MINT_TX_PER_S = 4.7
MINT_CHAIN_LENGTHS = {20: 5515, 21: 3463, 22: 1968, 23: 2937, 24: 3242}
MINT_LINK_GAP_S = 2.8
MINT_TX_VSIZE = 125
MINT_TX_FEE_SATS = 17
MINT_P2P = {
    "user_agent": "/mint:0.1/",
    "version": 70016,
    "services": NODE_NETWORK | NODE_WITNESS,
    "relay": False,
}

# Fleet: P2A rune mint chains, a funded root, links that each spend the previous anchor, and a tail
# that spends the last anchor plus a funded UTXO to CPFP the chain. Shapes come from a day with the
# fleet active, chains rebuilt through input txids; the whole chain arrives at once.
FLEET_NODE = "fleet-0"
FLEET_TX_PER_S = 1.73  # links only
FLEET_CHAIN_LINKS = {4: 47, 5: 156, 6: 869, 11: 444, 15: 3, 16: 881, 17: 156, 18: 41}
FLEET_LINK_GAP_S = 0
FLEET_LINK_VSIZES = {94: 27557, 107: 1040}
FLEET_LINK_FEERATE = 0.1  # sat/vB, rounded up: 10 sats at 94 vB, 11 at 107
P2A_DUST_SATS = 240
# continuous: the root anchor covers every link's fee and ends at the dust limit, as in the prod day.
# refill: the root anchor is one of the post's samples, and when the next link would take it below
# dust, a refill tx spends it plus a funded UTXO and recreates it at the root value.
FLEET_CHAIN_STYLES = {"continuous": 1, "refill": 1}
FLEET_REFILL_ANCHORS = {290: 1, 280: 1, 350: 1, 474: 1}
FLEET_ROOT_FEERATE = [(0, 0.1), (0.5, 0.1), (0.5, 1.2), (1, 1.2)]
FLEET_TAIL_FEERATE = [(0, 1.2), (0.5, 1.2), (0.6, 2.0), (0.7, 2.1), (0.8, 2.5), (0.9, 3.2), (1, 3.5)]
FLEET_P2P = {
    "user_agent": "/Sat0shi:31.0.0/",
    "version": 70015,
    "services": NODE_NONE,
    "relay": False,
}

# Sweeper: watches its own node's mempool and, for each fleet chain it sees, spends the anchor between
# two random consecutive links with a 66 vB tx that turns the anchor's whole value into fee.
SWEEPER_NODE = "sweeper-0"
SWEEPER_POLL_S = 1
SWEEPER_CHAIN_SHARE = 1.0  # chains swept, once each
SWEEPER_P2P = {
    "user_agent": "/p2aburn:1.0/",
    "version": 70016,
    "services": NODE_NONE,
    "relay": False,
}

# Phases, run by scripts/run_experiment.py. Background traffic and the miner run throughout; the
# warmup lets the mempool reach its steady depth before the control phase starts.
WARMUP_S = 5 * 60
PHASE_S = 17 * 60
PHASES = [
    ("control", ()),
    ("all", ("mint", "fleet", "sweeper")),
    ("no-sweeper", ("mint", "fleet")),
    ("all", ("mint", "fleet", "sweeper")),
    ("no-fleet", ("mint", "sweeper")),
    ("all", ("mint", "fleet", "sweeper")),
    ("no-mint", ("fleet", "sweeper")),
]
