#!/usr/bin/env python3
"""Write scripts/experiment/networks/<name>/network.yaml: miners, innocents and actor-run nodes, each with random
manual peers.

These addnode peers are only where each tank starts: warnet's Core image lets private IPs into
addrman, so tanks learn of each other through addr relay and also open Core's automatic outbound
connections.
"""

import argparse
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", default="cmpct")
    p.add_argument("--miners", type=int, default=3)
    p.add_argument("--innocents", type=int, default=30)
    p.add_argument("--actors", default="mint,fleet,sweeper", help="actors running their own node")
    p.add_argument("--min-outbound", type=int, default=1)
    p.add_argument("--max-outbound", type=int, default=2)
    p.add_argument("--seed", type=int, default=1)
    args = p.parse_args()

    rng = random.Random(args.seed)
    names = [f"miner-{i}" for i in range(args.miners)] + [f"node-{i:02d}" for i in range(args.innocents)]
    names += [f"{actor}-0" for actor in args.actors.split(",") if actor]
    lines = ["nodes:"]
    for name in names:
        peers = rng.sample([n for n in names if n != name], rng.randint(args.min_outbound, args.max_outbound))
        lines.append(f"  - name: {name}")
        lines.append("    addnode:")
        lines += [f"      - {peer}" for peer in sorted(peers)]
    out = HERE / "networks" / args.name / "network.yaml"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(names)} tanks)")


if __name__ == "__main__":
    main()
