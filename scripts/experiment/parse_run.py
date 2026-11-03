#!/usr/bin/env python3
"""Turn a run's logs (data/experiment/<run>/, from run_experiment.py) into CSVs in data/experiment/<run>/parsed/.

- blocks.csv: every block mined during the run, its miner and the phase it fell in. Phases go by
  height, not time, so wall-clock jumps on the nodes cannot move a block across a boundary.
- reconstructions.csv: one row per tank and compact block reconstruction (debug=cmpctblock).
- requested_txs.csv: every tx a tank had to request, with the actor that sent it. Taken from the
  blocktxn messages in the message capture, since the log names the txs only when fewer than 5 are
  missing; without a capture, falls back to those log lines.
- extrapool.csv: per tank and block, the txs that entered the extra pool since the tank's previous
  block and how many times that turned the ring over. Entries are inferred from the logs: txs
  rejected from a peer (mempoolrej) and txs replaced by one arriving from a peer (mempool).
- messages.csv: P2P messages per minute, tank, peer group, direction and type (debug=net).
- connections.csv: new connections per minute, tank and peer group (one per version message).
- rejections.csv: rejections per minute, tank, sending peer group, reason and actor of the tx.

Peer groups follow the user agent of the version message as in m4ycon's logpass (p2aburn,
txbroadcast, fleet), plus mint, and otherwise the role of the peer: by tank name for outbound
connections made by addnode, and otherwise by IP in pods.csv: the pod IP for inbound connections and
the Service IP for outbound ones the node found through addrman.
"""

import argparse
import collections
import csv
import datetime
import gzip
import io
import re
import struct
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE / "scenarios"))
from test_framework.messages import msg_blocktxn  # noqa: E402

TS = r"(?P<ts>\S+)"
RE = {
    "tip": re.compile(TS + r" UpdateTip: new best=(?P<hash>[0-9a-f]{64}) height=(?P<height>\d+)"),
    "recon": re.compile(
        TS + r" \[cmpctblock\] Successfully reconstructed block (?P<hash>[0-9a-f]{64}) with (?P<prefilled>\d+) txn "
        r"prefilled, (?P<mempool>\d+) txn from mempool \(incl at least (?P<extra>\d+) from extra pool\) and "
        r"(?P<requested>\d+) txn \((?P<requested_bytes>\d+) bytes\) requested"
    ),
    "required": re.compile(
        TS + r" \[cmpctblock\] Reconstructed block (?P<hash>[0-9a-f]{64}) required tx (?P<txid>[0-9a-f]{64})"
    ),
    "version": re.compile(
        r"\[net\] receive version message: (?P<ua>.*?): version (?P<version>\d+), .* peer=(?P<peer>\d+) "
        r"peeraddr=(?P<ip>[0-9.]+):"
    ),
    "added": re.compile(r"\[net\] Added connection to (?P<name>[a-z]+-\d+) peer=(?P<peer>\d+)"),
    "msg": re.compile(TS + r" \[net\] (?P<dir>received:|sending) (?P<type>\w+) \(\d+ bytes\) peer=(?P<peer>\d+)"),
    "rej": re.compile(
        TS + r" \[mempoolrej\] (?P<txid>[0-9a-f]{64}) \(wtxid=[0-9a-f]{64}\) from peer=(?P<peer>\d+) was not accepted: "
        r"(?P<reason>[a-z-]+(?: [a-z-]+)*)"
    ),
    "replacing": re.compile(
        TS + r" \[mempool\] replacing mempool tx (?P<old>[0-9a-f]{64}) .*New tx (?P<new>[0-9a-f]{64})"
    ),
    "accept": re.compile(TS + r" \[mempool\] AcceptToMemoryPool: peer=(?P<peer>\d+): accepted (?P<txid>[0-9a-f]{64})"),
}
ACTOR_LOG_TX = re.compile(r"(?: sent | sweep )(?:\S+ )*?txid=(?P<txid>[0-9a-f]{64})(?:.* role=(?P<role>[a-z]+))?")


def peer_group(ua, version, role):
    if ua.startswith("/p2aburn"):
        return "p2aburn"
    if ua.startswith("/txbroadcast"):
        return "txbroadcast"
    if ua == "/Sat0shi:31.0.0/" or (ua == "/Satoshi:31.0.0/" and version == 70015):
        return "fleet"
    if ua.startswith("/mint"):
        return "mint"
    return role or "other"


def read_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def actor_txids(run):
    """txid -> actor or actor:role, from every commander log of the run."""
    actors = {}
    for log in sorted((run / "commanders").glob("commander-*.log")):
        actor = log.name.split("-")[1]
        for line in open(log):
            m = ACTOR_LOG_TX.search(line)
            if m:
                actors[m["txid"]] = f"{actor}:{m['role']}" if m["role"] else actor
    return actors


def capture_records(tar_path, direction):
    """(time, msgtype, payload) of every message in a tank's message capture, per Core's CaptureMessage."""
    with tarfile.open(tar_path) as tar:
        for member in tar:
            if not member.name.endswith(f"msgs_{direction}.dat"):
                continue
            data = tar.extractfile(member).read()
            pos = 0
            while pos < len(data):
                micros, msgtype, size = struct.unpack_from("<Q12sI", data, pos)
                pos += 24
                yield micros, msgtype.rstrip(b"\0").decode(), data[pos : pos + size]
                pos += size


def requested_from_capture(tar_path, tank, actors, heights):
    # A node may ask up to three peers for the same block's missing txs, so each tx is kept once.
    rows, seen = [], set()
    for micros, msgtype, payload in capture_records(tar_path, "recv"):
        if msgtype != "blocktxn":
            continue
        msg = msg_blocktxn()
        msg.deserialize(io.BytesIO(payload))
        block = f"{msg.block_transactions.blockhash:064x}"
        if block not in heights:
            continue
        ts = datetime.datetime.fromtimestamp(micros / 1e6, datetime.timezone.utc).isoformat()
        for tx in msg.block_transactions.transactions:
            if (block, tx.txid_hex) in seen:
                continue
            seen.add((block, tx.txid_hex))
            rows.append({"tank": tank, "ts": ts, "height": heights[block], "hash": block, "txid": tx.txid_hex,
                         "actor": actors.get(tx.txid_hex, "")})
    return rows


def blocks(run, phases):
    rows = []
    for log in sorted((run / "commanders").glob("commander-miner-*.log")):
        for line in open(log):
            m = re.search(r"block height=(\d+) miner=(\S+) txs=(\d+) weight=(\d+) .*?hash=([0-9a-f]{64})", line)
            if m:
                height = int(m[1])
                phase = next((p for p in phases if int(p["start_height"]) < height <= int(p["end_height"])), None)
                rows.append({
                    "height": height, "hash": m[5], "miner": m[2], "txs": int(m[3]), "weight": int(m[4]),
                    "phase_index": phase["index"] if phase else "", "phase": phase["phase"] if phase else "",
                })
    return rows


def parse_tank(path, tank, roles, tank_roles, actors, ring):
    heights, recon, required, extra = {}, [], [], []
    peers = {}  # peer id -> group
    outbound = {}  # peer id -> tank name
    msgs = collections.Counter()  # (minute, peer, dir, type)
    versions = {}  # peer id -> minute of its version message
    rejs = collections.Counter()  # (minute, peer, reason, actor)
    accepted_from_peer = set()
    pending_entries = 0
    replacements = []  # (ts, new txid): resolved once the new tx's accept line is seen
    for line in gzip.open(path, "rt", errors="replace"):
        if "[net] received" in line or "[net] sending" in line:
            m = RE["msg"].match(line)
            if m:
                msgs[(m["ts"][:16], m["peer"], m["dir"].rstrip(":"), m["type"])] += 1
            continue
        if "receive version message" in line:
            m = RE["version"].search(line)
            if m:
                versions[m["peer"]] = line[:16]
                role = tank_roles.get(outbound.get(m["peer"])) or roles.get(m["ip"])
                peers[m["peer"]] = peer_group(m["ua"], int(m["version"]), role)
        elif "Added connection to" in line:
            m = RE["added"].search(line)
            if m:
                outbound[m["peer"]] = m["name"]
        elif "[mempoolrej]" in line:
            m = RE["rej"].match(line)
            if m:
                rejs[(m["ts"][:16], m["peer"], m["reason"], actors.get(m["txid"], ""))] += 1
                pending_entries += 1
        elif "replacing mempool tx" in line:
            m = RE["replacing"].match(line)
            if m:
                replacements.append(m["new"])
        elif "AcceptToMemoryPool: peer=" in line:
            m = RE["accept"].match(line)
            if m:
                accepted_from_peer.add(m["txid"])
                # Only replacements by a tx relayed from a peer feed the extra pool, not RPC ones.
                pending_entries += sum(1 for new in replacements if new == m["txid"])
                replacements = [new for new in replacements if new != m["txid"]]
        elif "UpdateTip: new best=" in line:
            m = RE["tip"].match(line)
            if m:
                heights[m["hash"]] = int(m["height"])
                extra.append({"tank": tank, "height": int(m["height"]), "ts": m["ts"], "entries": pending_entries,
                              "turns": round(pending_entries / ring, 2)})
                pending_entries = 0
                replacements = []
        elif "[cmpctblock]" in line:
            m = RE["recon"].match(line)
            if m:
                recon.append(m.groupdict())
                continue
            m = RE["required"].match(line)
            if m:
                required.append(m.groupdict())

    for row in recon:
        row.update(tank=tank, height=heights.get(row["hash"], ""))
    for row in required:
        row.update(tank=tank, height=heights.get(row["hash"], ""), actor=actors.get(row["txid"], ""))
    group = lambda peer: peers.get(peer, "other")  # noqa: E731
    msg_rows = collections.Counter()
    for (minute, peer, direction, typ), n in msgs.items():
        msg_rows[(minute, group(peer), direction, typ)] += n
    rej_rows = collections.Counter()
    for (minute, peer, reason, actor), n in rejs.items():
        rej_rows[(minute, group(peer), reason, actor)] += n
    conn_rows = collections.Counter((minute, group(peer)) for peer, minute in versions.items())
    return recon, required, extra, msg_rows, rej_rows, conn_rows


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", type=Path, help="data/experiment/<run> directory")
    args = p.parse_args()
    run = args.run
    out = run / "parsed"
    out.mkdir(exist_ok=True)

    pods = read_csv(run / "pods.csv")
    roles = {row["ip"]: row["role"] for row in pods if row["ip"]}
    roles.update({row["service_ip"]: row["role"] for row in pods if row.get("service_ip")})
    tank_roles = {row["pod"]: row["role"] for row in pods if not row["pod"].startswith("commander-")}
    ring = int(re.search(r"blockreconstructionextratxn=(\d+)", (run / "node-defaults.yaml").read_text())[1])
    actors = actor_txids(run)
    phases = read_csv(run / "phases.csv")

    block_rows = blocks(run, phases)
    run_blocks = {b["hash"]: b["height"] for b in block_rows}
    write_csv(out / "blocks.csv", block_rows, ["height", "hash", "miner", "txs", "weight", "phase_index", "phase"])

    recon_all, required_all, extra_all, msg_all, rej_all, conn_all = [], [], [], [], [], []
    for path in sorted((run / "tanks").glob("*.debug.log.gz")):
        tank = path.name.split(".")[0]
        print(f"parsing {tank}", flush=True)
        recon, required, extra, msgs, rejs, conns = parse_tank(path, tank, roles, tank_roles, actors, ring)
        role = tank_roles.get(tank, "")
        recon_all += [dict(r, role=role) for r in recon if r["hash"] in run_blocks]
        capture = run / "tanks" / f"{tank}.message_capture.tar.gz"
        if capture.exists():
            required = requested_from_capture(capture, tank, actors, run_blocks)
        required_all += [dict(r, role=role) for r in required if r["hash"] in run_blocks]
        extra_all += [dict(r, role=role) for r in extra]
        base = {"tank": tank, "role": role}
        for (minute, group, direction, typ), n in sorted(msgs.items()):
            msg_all.append(dict(base, minute=minute, group=group, direction=direction, type=typ, count=n))
        for (minute, group, reason, actor), n in sorted(rejs.items()):
            rej_all.append(dict(base, minute=minute, group=group, reason=reason, actor=actor, count=n))
        for (minute, group), n in sorted(conns.items()):
            conn_all.append(dict(base, minute=minute, group=group, count=n))

    write_csv(out / "reconstructions.csv", recon_all,
              ["tank", "role", "ts", "height", "hash", "prefilled", "mempool", "extra", "requested", "requested_bytes"])
    write_csv(out / "requested_txs.csv", required_all, ["tank", "role", "ts", "height", "hash", "txid", "actor"])
    write_csv(out / "extrapool.csv", extra_all, ["tank", "role", "height", "ts", "entries", "turns"])
    write_csv(out / "messages.csv", msg_all, ["minute", "tank", "role", "group", "direction", "type", "count"])
    write_csv(out / "rejections.csv", rej_all, ["minute", "tank", "role", "group", "reason", "actor", "count"])
    write_csv(out / "connections.csv", conn_all, ["minute", "tank", "role", "group", "count"])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
