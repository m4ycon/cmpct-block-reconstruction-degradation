"""Writes the transactions of each block of the given UTC days, asking the node with bitcoin-cli.

usage: fetch_blocks.py <workdir> <YYYY-MM-DD>...
Writes <workdir>/blocks/<day>.tsv, one row per non-coinbase tx, no header. Columns: height, block hash, index in
the block, txid, wtxid, vsize, fee (sats, 0 when the node has no undo data for the block), inputs, outputs,
inputs spending a P2A output, value of its P2A output (sats, empty if none), has a runestone (OP_RETURN
OP_13), all outputs are zero-valued, txid spent by its first P2A input. The P2A inputs are inferred when the
node has no undo data for the block (see spends_p2a).

The blocks of a day are those whose header time is in it, plus BLOCK_MARGIN on each side, since header times
can be off by an hour or so. A block the node already pruned is fetched back with getblockfrompeer and written
right away, since the node may prune it again soon. A day's file left incomplete by an interruption, or by a
block no peer sent, is resumed by the next run.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time

P2A = '51024e73'
BLOCK_MARGIN = 4
# one request at a time and a pause between them, rotating peers, so no peer sees a burst
FETCH_PAUSE = 2.0
FETCH_WAIT = 10.0


def cli(*args):
    return subprocess.run(['bitcoin-cli', *map(str, args)], capture_output=True, text=True)


def header_time(height):
    return json.loads(cli('getblockheader', cli('getblockhash', height).stdout.strip()).stdout)['time']


def first_height_at_or_after(timestamp):
    lo, hi = 0, int(cli('getblockcount').stdout)
    while lo < hi:
        mid = (lo + hi) // 2
        if header_time(mid) < timestamp:
            lo = mid + 1
        else:
            hi = mid
    return lo


def spends_p2a(vin, block_outputs, p2a_txs):
    if 'prevout' in vin:
        return vin['prevout']['scriptPubKey']['hex'] == P2A
    # without undo data (a block fetched back with getblockfrompeer) there are no prevouts. A parent in the same
    # block still shows the output spent. For one in an earlier block of the day only whether it created a P2A
    # output is known, so the input must also have neither scriptSig nor witness, as a P2A spend has; that alone
    # is not enough, since any script that needs no input data is spent that way. A parent from before the day's
    # first block counts as no P2A
    if vin['txid'] in block_outputs:
        return block_outputs[vin['txid']][vin['vout']]['scriptPubKey']['hex'] == P2A
    return vin['txid'] in p2a_txs and not vin['scriptSig']['hex'] and not vin.get('txinwitness')


def tx_rows(block, p2a_txs):
    block_outputs = {tx['txid']: tx['vout'] for tx in block['tx']}
    for i, tx in enumerate(block['tx'][1:], 1):
        p2a_in = [vin for vin in tx['vin'] if spends_p2a(vin, block_outputs, p2a_txs)]
        p2a_out = [round(out['value'] * 1e8) for out in tx['vout'] if out['scriptPubKey']['hex'] == P2A]
        runestone = any(out['scriptPubKey']['hex'].startswith('6a5d') for out in tx['vout'])
        yield [block['height'], block['hash'], i, tx['txid'], tx['hash'], tx['vsize'],
               round(tx.get('fee', 0) * 1e8), len(tx['vin']), len(tx['vout']), len(p2a_in),
               p2a_out[0] if p2a_out else '', int(runestone),
               int(all(out['value'] == 0 for out in tx['vout'])), p2a_in[0]['txid'] if p2a_in else '']


class Fetcher:
    """Fetches pruned blocks from outbound peers, skipping for good any peer that once let a request time out."""

    def __init__(self, slow_path):
        self.slow_path = slow_path
        self.slow = set(open(slow_path).read().split()) if os.path.exists(slow_path) else set()
        self.turn = 0

    def peers(self):
        return [p['id'] for p in json.loads(cli('getpeerinfo').stdout)
                if p.get('connection_type') in ('outbound-full-relay', 'block-relay-only')
                and 'NETWORK' in p.get('servicesnames', []) and str(p['id']) not in self.slow]

    def fetch(self, block_hash):
        peers = self.peers()
        if not peers:
            self.slow.clear()
            peers = self.peers()
        if not peers:
            return False
        self.turn += 1
        start = self.turn % len(peers)
        for peer in peers[start:] + peers[:start]:
            if cli('getblockfrompeer', block_hash, peer).returncode != 0:
                continue
            deadline = time.monotonic() + FETCH_WAIT
            while time.monotonic() < deadline:
                time.sleep(0.5)
                if cli('getblock', block_hash, 0).returncode == 0:
                    time.sleep(FETCH_PAUSE)
                    return True
            self.slow.add(str(peer))
            with open(self.slow_path, 'a') as f:
                f.write(f'{peer}\n')
        return False


def resume(path):
    """the last height left in path, or None, after dropping the last one written in case it was cut off
    mid-write; fetch_day writes heights in order and stops at a gap, so every height below it is done, including
    blocks with no tx but the coinbase, which leave no rows"""
    if not os.path.exists(path):
        return None
    with open(path) as f:
        rows = f.readlines()
    # a line cut off mid-write may not even hold its whole height, which would hide the block's earlier rows
    if rows and not rows[-1].endswith('\n'):
        rows.pop()
    last = rows[-1].split('\t', 1)[0] if rows else None
    kept = [row for row in rows if row.split('\t', 1)[0] != last]
    with open(path, 'w') as f:
        f.writelines(kept)
    return int(kept[-1].split('\t', 1)[0]) if kept else None


def fetch_day(day, path, fetcher):
    start = int(dt.datetime.fromisoformat(day + 'T00:00:00+00:00').timestamp())
    first = first_height_at_or_after(start) - BLOCK_MARGIN
    last = first_height_at_or_after(start + 86400) + BLOCK_MARGIN
    last_done = resume(path)
    # read back from the rows already written, so a resumed day comes out as one fetched in one go
    p2a_txs = set()
    if os.path.exists(path):
        with open(path) as f:
            p2a_txs = {row[3] for row in (line.split('\t') for line in f) if row[10] != ''}
    fetched = 0
    with open(path, 'a') as f:
        for height in range(first if last_done is None else last_done + 1, last + 1):
            block_hash = cli('getblockhash', height).stdout.strip()
            if cli('getblock', block_hash, 0).returncode != 0:
                if not fetcher.fetch(block_hash):
                    # the blocks after a gap would miss its P2A txs, so the day stops there for a rerun to resume
                    print(f'could not fetch {height} {block_hash}; run again to finish {day}', file=sys.stderr)
                    break
                fetched += 1
            r = cli('getblock', block_hash, 3)
            if r.returncode != 0:
                r = cli('getblock', block_hash, 2)
            rows = list(tx_rows(json.loads(r.stdout), p2a_txs))
            f.writelines('\t'.join(map(str, row)) + '\n' for row in rows)
            f.flush()
            p2a_txs.update(row[3] for row in rows if row[10] != '')
        else:
            print(day, 'heights', first, last, 'fetched from peers', fetched)


def main(workdir, days):
    os.makedirs(os.path.join(workdir, 'blocks'), exist_ok=True)
    fetcher = Fetcher(os.path.join(workdir, 'blocks', 'slow_peers.txt'))
    for day in days:
        fetch_day(day, os.path.join(workdir, 'blocks', day + '.tsv'), fetcher)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
