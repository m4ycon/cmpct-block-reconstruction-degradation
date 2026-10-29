"""Joins the per-day CSVs of analyze_day.py into the dashboard's CSVs.

usage: collect.py <workdir> <dest_dir> <YYYY-MM-DD>...
Writes to <dest_dir>, one block of rows per day in date order:
- blocks.csv: one row per compact block reconstructed: rebuild time (first "Saw new header" of the block to
  "Successfully reconstructed", ms), txs and bytes requested and txs taken from the extra pool as logged, txs
  missing by our count, those split by cause, and whether that first header came in a cmpctblock, which only
  a high-bandwidth peer sends unasked, or in a headers message.
- causes.csv: missing txs and their vsize per day and cause.
- block_types.csv: the day's block txs as P2A chain, 140 vB rune mint or other.
- chain_heads.csv: the first missing tx of each P2A chain the node refused for its fee, by its role and by what
  the mempool held instead.
- orphanage.csv: orphans stored, how many went into a full orphanage, and how long the missing chain txs that
  were orphans when their block came had waited (quartiles, minutes since a peer first sent them).
"""
import os
import sys

FILES = ['blocks', 'causes', 'block_types', 'chain_heads', 'orphanage']


def main(workdir, dest, days):
    os.makedirs(dest, exist_ok=True)
    for name in FILES:
        with open(os.path.join(dest, name + '.csv'), 'w', newline='') as out:
            for i, day in enumerate(sorted(days)):
                with open(os.path.join(workdir, 'days', day, name + '.csv'), newline='') as f:
                    header = f.readline()
                    if i == 0:
                        out.write(header)
                    out.writelines(f)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
