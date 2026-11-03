#!/usr/bin/env python3

import random
import time

import params
from commander import Commander
from test_framework.authproxy import JSONRPCException
from test_framework.messages import COIN, COutPoint, CTransaction, CTxIn, CTxOut
from test_framework.script import OP_RETURN, CScript
from test_framework.script_util import PAY_TO_ANCHOR
from traffic import Swarm, sleep_until, submit

# The 4-byte push makes the sweep 66 vB, the size the post reports.
SWEEP_OUTPUT = CScript([OP_RETURN, b"burn"])


class Sweeper(Commander):
    def set_test_params(self):
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Burn mid-chain P2A anchors of fleet chains seen in the sweeper's own node"
        parser.usage = "warnet run scripts/experiment/scenarios/sweeper.py [options]"
        parser.add_argument("--node", default=params.SWEEPER_NODE, help="the sweeper's own tank")
        parser.add_argument("--poll", type=float, default=params.SWEEPER_POLL_S, help="seconds between mempool polls")
        parser.add_argument("--share", type=float, default=params.SWEEPER_CHAIN_SHARE, help="share of chains swept")
        parser.add_argument("--max-peers", type=int, default=params.ACTOR_MAX_PEERS, help="tanks connected at once")
        parser.add_argument(
            "--lifetime", type=float, default=params.ACTOR_CONN_LIFETIME_S, help="mean seconds a connection lasts"
        )
        parser.add_argument("--duration", type=float, default=0, help="seconds to run (0: forever)")
        parser.add_argument("--seed", type=int, default=5)

    def run_test(self):
        self.rng = random.Random(self.options.seed)
        self.node = self.tanks[self.options.node]
        others = {name: tank for name, tank in self.tanks.items() if name != self.options.node}
        self.swarm = Swarm(
            others,
            self.rng,
            self.log,
            max_peers=self.options.max_peers,
            lifetime_s=self.options.lifetime,
            **params.SWEEPER_P2P,
        )
        # Mempool txs seen so far; for those with an anchor, its vout and value, and the tx whose
        # anchor they spend. Tails (spend an anchor, create none) trigger one decision per chain.
        self.seen = set()
        self.anchor = {}
        self.parent = {}
        self.sweeps = set()
        self.swept = self.skipped = 0
        self.log.info(f"watching {self.options.node} every {self.options.poll}s, sweeping {self.options.share}")

        end = time.time() + self.options.duration if self.options.duration else float("inf")
        while time.time() < end:
            started = time.time()
            self.swarm.maintain()
            self.poll()
            sleep_until(started + self.options.poll)
        self.log.info(f"done: swept={self.swept} skipped={self.skipped}")

    def poll(self):
        mempool = set(self.node.getrawmempool())
        for txid in self.seen - mempool:
            self.anchor.pop(txid, None)
            self.parent.pop(txid, None)
        self.seen &= mempool

        new = []
        for txid in mempool - self.seen:
            try:
                new.append(self.node.getrawtransaction(txid, True))
            except JSONRPCException:
                continue  # left the mempool between the two calls
        # Anchors first, so a child decoded before its parent in the same poll still finds it.
        for tx in new:
            self.seen.add(tx["txid"])
            anchors = [o for o in tx["vout"] if o["scriptPubKey"]["hex"] == PAY_TO_ANCHOR.hex()]
            if anchors:
                self.anchor[tx["txid"]] = (anchors[0]["n"], int(anchors[0]["value"] * COIN))
        tails = []
        for tx in new:
            spent = [(i["txid"], i["vout"]) for i in tx["vin"] if "txid" in i]
            parents = [p for p, n in spent if p in self.anchor and self.anchor[p][0] == n]
            if parents:
                self.parent[tx["txid"]] = parents[0]
                # A sweep also spends an anchor without creating one, but it ends no fleet chain.
                if tx["txid"] not in self.anchor and tx["txid"] not in self.sweeps:
                    tails.append(tx["txid"])
        for tail in tails:
            self.decide(tail)

    def decide(self, tail):
        # Walk up from the tail: [last link, ..., first link, root]. The anchors strictly between
        # two links are the mid-chain ones; the root's and the last link's are left alone.
        path = []
        txid = self.parent.get(tail)
        while txid:
            path.append(txid)
            txid = self.parent.get(txid)
        mid = path[1:-1]
        if not mid or self.rng.random() >= self.options.share:
            self.skipped += 1
            return
        target = self.rng.choice(mid)
        vout, value = self.anchor[target]
        sweep = CTransaction()
        sweep.vin = [CTxIn(COutPoint(int(target, 16), vout))]
        sweep.vout = [CTxOut(0, SWEEP_OUTPUT)]
        self.sweeps.add(sweep.txid_hex)
        own = submit(self.node, sweep)
        tanks = self.swarm.send(sweep)
        self.swept += 1
        self.log.info(
            f"sweep txid={sweep.txid_hex} anchor={target}:{vout} value={value} vsize={sweep.get_vsize()} "
            f"depth={len(path) - 1 - path.index(target)}/{len(path) - 1} tail={tail} own={own} "
            f"tanks={','.join(tanks)}"
        )


def main():
    Sweeper("").main()


if __name__ == "__main__":
    main()
