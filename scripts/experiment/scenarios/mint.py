#!/usr/bin/env python3

import random
from decimal import Decimal

import params
from commander import Commander
from test_framework.messages import COIN
from test_framework.wallet import MiniWallet
from traffic import Swarm, interleave_chains, submit, weighted_mean


class Mint(Commander):
    def set_test_params(self):
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Push rune mint chains without P2A to random tanks over raw P2P"
        parser.usage = "warnet run scripts/experiment/scenarios/mint.py [options]"
        parser.add_argument("--rate", type=float, default=params.MINT_TX_PER_S, help="txs per second")
        parser.add_argument(
            "--link-gap", type=float, default=params.MINT_LINK_GAP_S, help="mean seconds between links of a chain"
        )
        parser.add_argument("--node", default=params.MINT_NODE, help="the actor's own tank")
        parser.add_argument("--max-peers", type=int, default=params.ACTOR_MAX_PEERS, help="tanks connected at once")
        parser.add_argument(
            "--lifetime", type=float, default=params.ACTOR_CONN_LIFETIME_S, help="mean seconds a connection lasts"
        )
        parser.add_argument("--duration", type=float, default=0, help="seconds to run (0: forever)")
        parser.add_argument("--seed", type=int, default=3)

    def run_test(self):
        self.rng = random.Random(self.options.seed)
        self.node = self.tanks[self.options.node]
        others = {name: tank for name, tank in self.tanks.items() if name != self.options.node}
        self.wallet = MiniWallet(self.node, tag_name="mint")
        self.roots = self.wallet.get_utxos(confirmed_only=True)
        self.lengths = params.MINT_CHAIN_LENGTHS
        self.swarm = Swarm(
            others,
            self.rng,
            self.log,
            max_peers=self.options.max_peers,
            lifetime_s=self.options.lifetime,
            **params.MINT_P2P,
        )
        self.sent = 0
        chains_per_s = self.options.rate / weighted_mean(self.lengths)
        self.log.info(f"{len(self.roots)} roots, {self.options.rate} tx/s, {chains_per_s:.2f} chains/s")
        interleave_chains(self.rng, chains_per_s, self.options.link_gap, self.rune_chain, self.options.duration)
        self.log.info(f"done: sent={self.sent}")

    def rune_chain(self):
        utxo = self.roots.pop()
        length = self.rng.choices(list(self.lengths), weights=list(self.lengths.values()))[0]
        fee = Decimal(params.MINT_TX_FEE_SATS) / COIN
        for link in range(length):
            tx = self.wallet.create_self_transfer(utxo_to_spend=utxo, fee=fee, target_vsize=params.MINT_TX_VSIZE)
            own = submit(self.node, tx["tx"])
            tanks = self.swarm.send(tx["tx"])
            self.sent += 1
            self.log.info(f"sent txid={tx['txid']} link={link}/{length} own={own} tanks={','.join(tanks)}")
            utxo = tx["new_utxo"]
            yield True


def main():
    Mint("").main()


if __name__ == "__main__":
    main()
