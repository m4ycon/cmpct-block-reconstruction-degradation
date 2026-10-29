"""Tells the story of one missing P2A chain from the log, for the dashboard's "One chain" timeline.

usage: example_chain.py <workdir> <dest_dir> <YYYY-MM-DD> <txid>
<txid> is the first missing tx of a chain (a link swept by a sweeper) in a block of that day; analyze_day.py must
have run for the day. Writes <dest_dir>/example_chain.csv with the first time of each step: the link's parent
and the link accepted, the sweeper replacing the link, the link sent again and rejected, its descendants stored
as orphans, the block arriving and its reconstruction. The actors are those of the dashboard's chain, written
in, not read from the log.
"""
import collections
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_day import timestamp, window_lines  # noqa: E402

RX_TXID = re.compile(r'[0-9a-f]{64}')
RX_SWEEPER = re.compile(r'New tx \S+ \(wtxid=\S+ fees=(\d+), vsize=(\d+)\)')
RX_RBF_FEES = re.compile(r'; ([\d.]+) < ([\d.]+)')
RX_REQUESTED = re.compile(r'and (\d+) txn \((\d+) bytes\) requested')


def main(workdir, dest, day, head):
    txs = {}
    with open(os.path.join(workdir, 'blocks', day + '.tsv')) as f:
        for line in f:
            row = line.rstrip('\n').split('\t')
            txs[row[3]] = dict(height=row[0], block=row[1], vsize=row[5], fee=row[6], anchor=row[10],
                               parent=row[13])
    with open(os.path.join(workdir, 'days', day, 'missing.tsv')) as f:
        missing = {row['txid']: row for row in csv.DictReader(f, delimiter='\t')}
    children = collections.defaultdict(list)
    for txid in missing:
        children[txs[txid]['parent']].append(txid)
    chain, todo = [], [head]
    while todo:
        txid = todo.pop()
        chain.append(txid)
        todo += children.get(txid, [])
    parent = txs[head]['parent']
    watched = set(chain) | {parent}
    block = txs[head]['block']

    lines = []
    for line in window_lines(workdir, day, history_days=1):
        if block in line and ('Initializing' in line or 'reconstructed' in line):
            lines.append((timestamp(line), 'block', line))
            continue
        m = RX_TXID.search(line, 28)
        if m and m.group(0) in watched:
            lines.append((timestamp(line), m.group(0), line))

    steps = {}
    descendants = len(chain) - 1
    for t, who, line in sorted(lines):
        if who == parent and 'accepted' in line:
            steps.setdefault('parent', (t, 'Parent link accepted', 'fleet peer',
                                        f"anchor {txs[parent]['anchor']} sats"))
        elif who == head and 'AcceptToMemoryPool' in line:
            steps.setdefault('link', (t, 'Link accepted', 'fleet peer',
                                      f"fee {txs[head]['fee']} sats, {txs[head]['vsize']} vB"))
        elif who == head and 'replacing mempool tx' in line and 'sweeper' not in steps:
            fee, vsize = RX_SWEEPER.search(line).groups()
            steps['sweeper'] = (t, 'Sweeper replaces the link', '/p2aburn:1.0/ peer',
                                f'fee {fee} sats (the whole anchor), {vsize} vB')
        elif who == head and 'was not accepted' in line and 'rejected' not in steps:
            ours, theirs = RX_RBF_FEES.search(line).groups()
            steps['rejected'] = (t, 'Link sent again, rejected', 'fleet peer',
                                 f'RBF: {round(float(ours) * 1e8)} < {round(float(theirs) * 1e8)} sats')
        elif who in chain and who != head and 'stored orphan' in line:
            roles = collections.Counter(missing[x]['role'] for x in chain if x != head)
            detail = f'{descendants} txs: ' + ', '.join(f'{n} {r}' for r, n in roles.most_common())
            steps.setdefault('orphans', (t, 'Descendants become orphans', 'fleet peers', detail))
        elif who == 'block' and 'Initializing' in line:
            detail = f"height {txs[head]['height']}, link and all {descendants} descendants inside"
            steps.setdefault('block', (t, 'Block arrives as a compact block', 'miner', detail))
        elif who == 'block' and 'reconstructed' in line and 'done' not in steps:
            n, nbytes = RX_REQUESTED.search(line).groups()
            steps['done'] = (t, 'Round trip done', 'our node',
                             f'{n} txs requested ({nbytes} bytes) for the whole block')

    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, 'example_chain.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['time', 'step', 'actor', 'detail'])
        w.writerows(sorted(steps.values()))


if __name__ == '__main__':
    main(*sys.argv[1:5])
