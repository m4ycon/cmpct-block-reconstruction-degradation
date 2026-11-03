#!/usr/bin/env python3

import params
from commander import Commander
from test_framework.messages import COIN, COutPoint, CTransaction, CTxIn, CTxOut
from test_framework.wallet import MiniWallet

# Keeps a fanout tx under MAX_STANDARD_TX_WEIGHT (100 kvB) at 43 vB per P2TR output.
FANOUT_OUTPUTS = 2000
# generateblock bypasses -blockmaxweight but not the 4M WU consensus limit; a full fanout is ~344k WU.
FANOUTS_PER_BLOCK = 10
FANOUT_FEE = 100_000


class Bootstrap(Commander):
    def set_test_params(self):
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Mine past coinbase maturity and fund each actor's tagged MiniWallet"
        parser.usage = "warnet run scripts/experiment/scenarios/bootstrap.py [options]"
        for actor, n in params.FUNDING_UTXOS.items():
            parser.add_argument(f"--{actor}", type=int, default=n, help=f"UTXOs for {actor}")
        parser.add_argument("--value", type=int, default=params.FUNDING_UTXO_SATS, help="sats per funded UTXO")

    def run_test(self):
        self.wait_for_tanks_connected()
        node = self.tanks["miner-0"]
        funder = MiniWallet(node, tag_name="funding")

        # Each actor scenario rebuilds its wallet from this tag, so no state has to cross pods.
        fanouts = []
        for actor in params.FUNDING_UTXOS:
            spk = MiniWallet(node, tag_name=actor).get_output_script()
            n = getattr(self.options, actor)
            fanouts += [(spk, min(FANOUT_OUTPUTS, n - i)) for i in range(0, n, FANOUT_OUTPUTS)]

        self.generate(funder, 100 + len(fanouts), sync_fun=self.no_op)
        txs = [self.fanout(funder, spk, n) for spk, n in fanouts]
        for i in range(0, len(txs), FANOUTS_PER_BLOCK):
            self.generateblock(node, funder.get_address(), txs[i : i + FANOUTS_PER_BLOCK], sync_fun=self.no_op)
        self.sync_blocks(self.nodes, timeout=300)

        height = node.getblockcount()
        for actor in params.FUNDING_UTXOS:
            utxos = MiniWallet(node, tag_name=actor).get_utxos(mark_as_spent=False)
            self.log.info(f"{actor}: {len(utxos)} UTXOs, {sum(u['value'] for u in utxos)} BTC")
        self.log.info(f"bootstrap done at height {height}")

    def fanout(self, funder, spk, n):
        coinbase = funder.get_utxo()
        value_in = int(coinbase["value"] * COIN)
        tx = CTransaction()
        tx.vin = [CTxIn(COutPoint(int(coinbase["txid"], 16), coinbase["vout"]))]
        tx.vout = [CTxOut(self.options.value, spk) for _ in range(n)]
        tx.vout.append(CTxOut(value_in - n * self.options.value - FANOUT_FEE, funder.get_output_script()))
        funder.sign_tx(tx)
        return tx.serialize().hex()


def main():
    Bootstrap("").main()


if __name__ == "__main__":
    main()
