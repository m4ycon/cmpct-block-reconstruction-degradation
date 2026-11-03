#!/usr/bin/env python3

import random
import time

import params
from commander import Commander
from test_framework.authproxy import JSONRPCException
from test_framework.wallet import MiniWallet
from traffic import sleep_until


class Miner(Commander):
    def set_test_params(self):
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Mine one block per interval on a random miner tank, from its latest block template"
        parser.usage = "warnet run scripts/experiment/scenarios/miner.py [options]"
        parser.add_argument("--interval", type=float, default=params.BLOCK_INTERVAL_S, help="seconds between blocks")
        parser.add_argument(
            "--template-refresh", type=float, default=params.MINER_TEMPLATE_REFRESH_S,
            help="seconds between block templates (0: mine the mempool as it is at the block)",
        )
        parser.add_argument("--blocks", type=int, default=0, help="stop after this many blocks (0: never)")
        parser.add_argument("--seed", type=int, default=1, help="seed for picking the miner of each block")

    def run_test(self):
        self.miners = sorted(name for name in self.tanks if name.startswith("miner-"))
        addrs = {name: MiniWallet(self.tanks[name], tag_name=f"coinbase-{name}").get_address() for name in self.miners}
        rng = random.Random(self.options.seed)
        refresh = self.options.template_refresh
        self.log.info(f"mining every {self.options.interval}s on {self.miners}, templates every {refresh}s")

        # Deadlines are absolute so the time spent mining and relaying does not stretch the interval.
        start = time.time()
        mined = 0
        self.refresh_templates()
        while not self.options.blocks or mined < self.options.blocks:
            due = start + (mined + 1) * self.options.interval
            while refresh and self.fetched + refresh < due:
                sleep_until(self.fetched + refresh)
                self.refresh_templates()
            sleep_until(due)
            name = rng.choice(self.miners)
            block_hash, age = self.mine(name, addrs[name]) if refresh else (self.mine_mempool(name, addrs[name]), 0)
            block = self.tanks[name].getblock(block_hash, 1)
            mined += 1
            self.log.info(
                f"block height={block['height']} miner={name} txs={block['nTx']} "
                f"weight={block['weight']} template_age={age:.1f} hash={block_hash}"
            )
            # Pools build a new template as soon as a block arrives, not at the next refresh.
            if refresh:
                self.refresh_templates()

    def refresh_templates(self):
        self.fetched = time.time()
        self.templates = {}
        for name in self.miners:
            template = self.tanks[name].getblocktemplate({"rules": ["segwit"]})
            self.templates[name] = [tx["txid"] for tx in template["transactions"]]

    def mine(self, name, addr):
        """Mine the miner's latest template, minus the txs that have left its mempool since."""
        node = self.tanks[name]
        age = time.time() - self.fetched
        for attempt in range(3):
            mempool = set(node.getrawmempool())
            txids = [txid for txid in self.templates[name] if txid in mempool]
            try:
                return self.generateblock(node, addr, txids, sync_fun=self.no_op)["hash"], age
            except JSONRPCException as e:
                # A tx can leave the mempool between the two calls, as when a sweep replaces it.
                self.log.info(f"template for {name} went stale ({e.error['message']}), retrying")
        return self.mine_mempool(name, addr), age

    def mine_mempool(self, name, addr):
        return self.generatetoaddress(self.tanks[name], 1, addr, sync_fun=self.no_op)[0]


def main():
    Miner("").main()


if __name__ == "__main__":
    main()
