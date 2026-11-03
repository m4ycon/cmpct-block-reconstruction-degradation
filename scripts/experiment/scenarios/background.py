#!/usr/bin/env python3

import math
import random
from decimal import Decimal

import params
from commander import Commander
from test_framework.authproxy import JSONRPCException
from test_framework.messages import COIN
from test_framework.wallet import MiniWallet
from traffic import interleave_chains, inverse_cdf, weighted_mean

# Below this vsize MiniWallet cannot pad (the padding output alone takes 10 vB), so it sends 104 vB.
MIN_PADDED_VSIZE = 114
MIN_CHANGE_SATS = 1000


class Background(Commander):
    def set_test_params(self):
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Send mainnet-shaped wallet traffic through RPC on random innocent tanks"
        parser.usage = "warnet run scripts/experiment/scenarios/background.py [options]"
        parser.add_argument("--rate", type=float, default=params.BACKGROUND_TX_PER_S, help="txs per second")
        parser.add_argument(
            "--link-gap", type=float, default=params.BACKGROUND_LINK_GAP_S, help="mean seconds between cluster links"
        )
        parser.add_argument("--duration", type=float, default=0, help="seconds to run (0: forever)")
        parser.add_argument("--seed", type=int, default=2)

    def run_test(self):
        self.rng = random.Random(self.options.seed)
        self.innocents = sorted(name for name in self.tanks if name.startswith("node-"))
        self.wallet = MiniWallet(self.tanks[self.innocents[0]], tag_name="background")
        self.roots = self.wallet.get_utxos(confirmed_only=True)
        self.sizes = params.BACKGROUND_CLUSTER_SIZES
        self.sent = self.failed = 0
        chains_per_s = self.options.rate / weighted_mean(self.sizes)
        self.log.info(f"{len(self.roots)} roots, {self.options.rate} tx/s, {chains_per_s:.2f} clusters/s")
        interleave_chains(self.rng, chains_per_s, self.options.link_gap, self.cluster, self.options.duration)
        self.log.info(f"done: sent={self.sent} failed={self.failed}")

    def cluster(self):
        # One user, one node: every link goes through the same tank, as a wallet would.
        name = self.rng.choice(self.innocents)
        node = self.tanks[name]
        utxo = self.roots.pop()
        size = self.rng.choices(list(self.sizes), weights=list(self.sizes.values()))[0]
        for link in range(size):
            vsize = round(inverse_cdf(params.BACKGROUND_VSIZE, self.rng.random()))
            vsize = vsize if vsize >= MIN_PADDED_VSIZE else 0
            # bulk_vout cannot hit the two sizes where the padding's length prefix grows to 3 bytes.
            vsize = vsize + 2 if vsize in (366, 367) else vsize
            fee = math.ceil(inverse_cdf(params.BACKGROUND_FEERATE, self.rng.random()) * (vsize or 104))
            if int(utxo["value"] * COIN) - fee < MIN_CHANGE_SATS:
                return
            tx = self.wallet.create_self_transfer(utxo_to_spend=utxo, fee=Decimal(fee) / COIN, target_vsize=vsize)
            try:
                node.sendrawtransaction(hexstring=tx["hex"], maxfeerate=0)
            except JSONRPCException as e:
                self.failed += 1
                self.log.info(f"rejected tank={name} txid={tx['txid']} link={link}: {e.error['message']}")
                return
            self.sent += 1
            self.log.info(
                f"sent tank={name} txid={tx['txid']} link={link}/{size} vsize={tx['tx'].get_vsize()} fee={fee}"
            )
            utxo = tx["new_utxo"]
            yield True


def main():
    Background("").main()


if __name__ == "__main__":
    main()
