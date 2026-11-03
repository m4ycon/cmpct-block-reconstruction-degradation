#!/usr/bin/env python3

import math
import random

import params
from commander import Commander
from test_framework.messages import COIN, COutPoint, CTransaction, CTxIn, CTxInWitness, CTxOut
from test_framework.script import OP_13, OP_RETURN, CScript
from test_framework.script_util import PAY_TO_ANCHOR
from test_framework.wallet import MiniWallet
from traffic import Swarm, interleave_chains, inverse_cdf, submit, weighted_mean

# Runestone mint of rune 1:0 (tag 20 = mint, then block 1, tx 0). Core never parses it, so the
# zero padding after it only sets the link's size.
RUNE_MINT = bytes([20, 1, 20, 0])


class Fleet(Commander):
    def set_test_params(self):
        self.num_nodes = 1

    def add_options(self, parser):
        parser.description = "Push P2A rune mint chains to random tanks over raw P2P, as the fleet did"
        parser.usage = "warnet run scripts/experiment/scenarios/fleet.py [options]"
        parser.add_argument("--rate", type=float, default=params.FLEET_TX_PER_S, help="links per second")
        parser.add_argument(
            "--link-gap", type=float, default=params.FLEET_LINK_GAP_S, help="mean seconds between chain txs"
        )
        parser.add_argument("--node", default=params.FLEET_NODE, help="the actor's own tank")
        parser.add_argument("--max-peers", type=int, default=params.ACTOR_MAX_PEERS, help="tanks connected at once")
        parser.add_argument(
            "--lifetime", type=float, default=params.ACTOR_CONN_LIFETIME_S, help="mean seconds a connection lasts"
        )
        parser.add_argument("--duration", type=float, default=0, help="seconds to run (0: forever)")
        parser.add_argument("--seed", type=int, default=4)

    def run_test(self):
        self.rng = random.Random(self.options.seed)
        self.node = self.tanks[self.options.node]
        others = {name: tank for name, tank in self.tanks.items() if name != self.options.node}
        self.wallet = MiniWallet(self.node, tag_name="fleet")
        self.funded = self.wallet.get_utxos(confirmed_only=True)
        self.lengths = params.FLEET_CHAIN_LINKS
        self.swarm = Swarm(
            others,
            self.rng,
            self.log,
            max_peers=self.options.max_peers,
            lifetime_s=self.options.lifetime,
            **params.FLEET_P2P,
        )
        self.sent = 0
        chains_per_s = self.options.rate / weighted_mean(self.lengths)
        self.log.info(f"{len(self.funded)} funded UTXOs, {self.options.rate} links/s, {chains_per_s:.2f} chains/s")
        interleave_chains(self.rng, chains_per_s, self.options.link_gap, self.p2a_chain, self.options.duration)
        self.log.info(f"done: sent={self.sent}")

    def p2a_chain(self):
        n_links = self.rng.choices(list(self.lengths), weights=list(self.lengths.values()))[0]
        link_vsizes = params.FLEET_LINK_VSIZES
        vsizes = self.rng.choices(list(link_vsizes), weights=list(link_vsizes.values()), k=n_links)
        fees = [math.ceil(v * params.FLEET_LINK_FEERATE) for v in vsizes]

        styles = params.FLEET_CHAIN_STYLES
        style = self.rng.choices(list(styles), weights=list(styles.values()))[0]
        if style == "refill":
            anchors = params.FLEET_REFILL_ANCHORS
            refill_value = self.rng.choices(list(anchors), weights=list(anchors.values()))[0]
            anchor_value = refill_value
        else:
            anchor_value = params.P2A_DUST_SATS + sum(fees)
        root = self.funded_tx([], [CTxOut(anchor_value, PAY_TO_ANCHOR)], params.FLEET_ROOT_FEERATE)
        yield self.push(root, "root", n_links, style)

        anchor = COutPoint(int(root.txid_hex, 16), 0)
        for i, (vsize, fee) in enumerate(zip(vsizes, fees)):
            if anchor_value - fee < params.P2A_DUST_SATS:
                refill = self.funded_tx(
                    [(anchor, anchor_value)], [CTxOut(refill_value, PAY_TO_ANCHOR)], params.FLEET_ROOT_FEERATE
                )
                yield self.push(refill, "refill", n_links, style)
                anchor, anchor_value = COutPoint(int(refill.txid_hex, 16), 0), refill_value
            anchor_value -= fee
            link = CTransaction()
            link.vin = [CTxIn(anchor)]
            link.vout = [CTxOut(anchor_value, PAY_TO_ANCHOR), CTxOut(0, CScript([OP_RETURN, OP_13, RUNE_MINT]))]
            pad = vsize - link.get_vsize()
            link.vout[1].scriptPubKey = CScript([OP_RETURN, OP_13, RUNE_MINT + bytes(pad)])
            assert link.get_vsize() == vsize, (link.get_vsize(), vsize)
            yield self.push(link, f"link{i}", n_links, style)
            anchor = COutPoint(int(link.txid_hex, 16), 0)

        tail = self.funded_tx([(anchor, anchor_value)], [], params.FLEET_TAIL_FEERATE)
        yield self.push(tail, "tail", n_links, style)

    def funded_tx(self, anchors, outputs, feerate_points):
        """A tx spending the given anchors plus one funded UTXO, paying the change back to the fleet."""
        utxo = self.funded.pop()
        tx = CTransaction()
        tx.vin = [CTxIn(outpoint) for outpoint, _ in anchors]
        tx.vin.append(CTxIn(COutPoint(int(utxo["txid"], 16), utxo["vout"])))
        tx.vout = outputs + [CTxOut(0, self.wallet.get_output_script())]
        # P2A inputs must spend with an empty witness; only the funded input carries MiniWallet's.
        tx.wit.vtxinwit = [CTxInWitness() for _ in tx.vin]
        tx.wit.vtxinwit[-1].scriptWitness.stack = self.op_true_witness()
        fee = math.ceil(inverse_cdf(feerate_points, self.rng.random()) * tx.get_vsize())
        value_in = int(utxo["value"] * COIN) + sum(value for _, value in anchors)
        tx.vout[-1].nValue = value_in - sum(o.nValue for o in outputs) - fee
        return tx

    def op_true_witness(self):
        leaf = list(self.wallet._taproot_info.leaves.values())[0]
        control = bytes([leaf.version | self.wallet._taproot_info.negflag]) + self.wallet._taproot_info.internal_pubkey
        return [leaf.script, control]

    def push(self, tx, role, n_links, style):
        own = submit(self.node, tx)
        tanks = self.swarm.send(tx)
        self.sent += 1
        self.log.info(
            f"sent txid={tx.txid_hex} role={role} links={n_links} style={style} vsize={tx.get_vsize()} "
            f"own={own} tanks={','.join(tanks)}"
        )
        return True


def main():
    Fleet("").main()


if __name__ == "__main__":
    main()
