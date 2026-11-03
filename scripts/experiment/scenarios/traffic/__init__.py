"""Building blocks shared by the scenarios that generate transactions."""

import bisect
import heapq
import time

from test_framework.authproxy import JSONRPCException
from test_framework.messages import msg_tx, msg_verack
from test_framework.p2p import P2PInterface

# A connection still not open after this long is given up and replaced.
CONNECT_TIMEOUT_S = 10


def sleep_until(deadline):
    """Sleep until a wall-clock time.

    time.sleep runs on the monotonic clock, which can run at a different rate from the NTP-disciplined
    wall clock (as in a WSL2 VM). Sleeping in short slices against time.time() keeps every schedule
    on the wall clock that the nodes' logs use.
    """
    while (left := deadline - time.time()) > 0:
        time.sleep(min(left, 0.5))


def inverse_cdf(points, u):
    """Value at quantile u of a distribution given as sorted (quantile, value) points."""
    i = min(bisect.bisect_right([q for q, _ in points], u), len(points) - 1)
    (q0, v0), (q1, v1) = points[i - 1], points[i]
    return v0 + (v1 - v0) * (u - q0) / (q1 - q0)


def weighted_mean(weights):
    return sum(k * w for k, w in weights.items()) / sum(weights.values())


def interleave_chains(rng, chains_per_s, link_gap_s, start_chain, duration_s):
    """Start chains as a Poisson process and advance each by one link at a time.

    start_chain() returns an iterator whose next() sends one link and yields True. Gaps between a
    chain's links are exponential, so chains overlap in time the way independent senders do; a zero
    gap sends the whole chain at once.
    """
    now = time.time()
    end = now + duration_s if duration_s else float("inf")
    next_start = now + rng.expovariate(chains_per_s)
    pending = []  # (due, seq, chain); seq keeps the heap from comparing iterators
    seq = 0
    while True:
        due = min(next_start, pending[0][0]) if pending else next_start
        if due >= end:
            return
        sleep_until(due)
        if pending and pending[0][0] <= next_start:
            _, _, chain = heapq.heappop(pending)
        else:
            chain = start_chain()
            next_start += rng.expovariate(chains_per_s)
        if next(chain, False):
            seq += 1
            gap = rng.expovariate(1 / link_gap_s) if link_gap_s else 0
            heapq.heappush(pending, (due + gap, seq, chain))


def submit(node, tx):
    """Hand tx to an actor's own node, which relays it like any other; returns "accepted" or the reason."""
    try:
        node.sendrawtransaction(hexstring=tx.serialize().hex(), maxfeerate=0)
        return "accepted"
    except JSONRPCException as e:
        return e.error["message"].replace(" ", "-")


class Pusher(P2PInterface):
    """Raw P2P peer that pushes txs unannounced and never asks for what the node announces.

    Like the actors in the post, it only ever sends version, verack, tx and pong.
    """

    def __init__(self, *, user_agent, version, services, relay):
        super().__init__()
        self.user_agent = user_agent
        self.version = version
        self.services = services
        self.relay_flag = relay

    def peer_connect_send_version(self, services):
        super().peer_connect_send_version(self.services)
        vt = self.on_connection_send_msg
        vt.nVersion = self.version
        vt.strSubVer = self.user_agent
        vt.relay = int(self.relay_flag)

    def on_version(self, message):
        self.send_without_ping(msg_verack())
        self.nServices = message.nServices
        self.relay = message.relay

    def on_inv(self, message):
        pass


class Swarm:
    """Up to max_peers Pushers on random tanks, each kept for an exponential lifetime, then replaced.

    The post's actors never ask a node for addresses, so the tank list stands in for wherever they
    get them, and rotation stands in for their churn.
    """

    def __init__(self, tanks, rng, log, *, max_peers, lifetime_s, **version):
        self.tanks = tanks
        self.rng = rng
        self.log = log
        self.max_peers = max_peers
        self.lifetime_s = lifetime_s
        self.version = version
        self.peers = {}  # tank name -> (peer, opened at, expires at)
        self.maintain()
        self.log.info(f"pushing to up to {max_peers} tanks as {version['user_agent']}")

    def maintain(self):
        now = time.time()
        for name, (peer, opened, expires) in list(self.peers.items()):
            dead = not peer.is_connected and (peer.message_count["verack"] or now - opened > CONNECT_TIMEOUT_S)
            if now >= expires or dead:
                peer.peer_disconnect()
                del self.peers[name]
                self.log.info(f"disconnect tank={name} reason={'expired' if now >= expires else 'dropped'}")
        free = [name for name in self.tanks if name not in self.peers]
        for name in self.rng.sample(free, min(len(free), self.max_peers - len(self.peers))):
            self.peers[name] = (self.connect(name), now, now + self.rng.expovariate(1 / self.lifetime_s))
            self.log.info(f"connect tank={name}")

    def connect(self, name):
        node = self.tanks[name]
        peer = Pusher(**self.version)
        peer.peer_connect(
            dstaddr=node.rpchost,
            dstport=node.p2pport,
            net=node.chain,
            timeout_factor=1,
            send_version=True,
            supports_v2_p2p=False,
        )()
        return peer

    def send(self, tx):
        """Send to every peer past the handshake; returns the tanks it went to."""
        self.maintain()
        msg = msg_tx(tx)
        sent = []
        for name, (peer, _, _) in self.peers.items():
            # A node ignores a tx that arrives before the handshake ends, so skip peers still in it.
            if peer.is_connected and peer.message_count["verack"]:
                peer.send_without_ping(msg)
                sent.append(name)
        return sent
