"""Works out, for each compact block of one UTC day, which of its txs our mempool lacked when it arrived, and why.

usage: analyze_day.py <workdir> <YYYY-MM-DD>
Reads <workdir>/blocks/<day>.tsv (fetch_blocks.py) and the lines of <workdir>/extract/ (extract.sh) from
HISTORY_DAYS before the day to its end, in time order. Writes to <workdir>/days/<day>/:
- blocks.csv: per reconstructed block, its rebuild time, how we first heard of it, what the log says it requested
  and took from the extra pool, how many txs it was missing by our count, and those split by cause (CAUSES).
- causes.csv, block_types.csv, chain_heads.csv, orphanage.csv: the day's row(s) of each dashboard CSV (see
  collect.py).
- missing.tsv: every missing tx of every block that arrived in the window, with its role and why it was missing.

A tx is missing when, before the cmpctblock arrived, the log never shows it accepted, or shows it replaced,
rejected or stored as an orphan after its last acceptance, or accepted under another wtxid.
"""
import collections
import csv
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys

# low-fee txs can wait in the mempool for days, so a block's missing set needs that much history
HISTORY_DAYS = 3
ORPHANAGE_LIMIT = 3000
CAUSES = ['p2a_sweeper_won_here', 'p2a_chain_won_here', 'p2a_root_conflict', 'p2a_other', 'rune140', 'other']
P2A_CAUSES = CAUSES[:4]

RX_ACCEPTED = re.compile(r'AcceptToMemoryPool: peer=\d+: accepted ([0-9a-f]{64}) \(wtxid=([0-9a-f]{64})')
RX_REPLACED = re.compile(r'replacing mempool tx ([0-9a-f]{64}) \(wtxid=[0-9a-f]{64}.*New tx ([0-9a-f]{64})')
RX_REJECTED = re.compile(r'([0-9a-f]{64}) \(wtxid=[0-9a-f]{64}\) from peer=\d+ was not accepted: ([^,(]+)')
RX_ORPHAN_STORED = re.compile(r'stored orphan tx ([0-9a-f]{64})')
RX_ORPHAN_REMOVED = re.compile(r'removed orphan tx ([0-9a-f]{64})')
RX_HEADER = re.compile(r'Saw new (cmpctblock )?header hash=([0-9a-f]{64})')
RX_ARRIVAL = re.compile(r'Initializing PartiallyDownloadedBlock for block ([0-9a-f]{64})')
RX_RECONSTRUCTED = re.compile(
    r'Successfully reconstructed block ([0-9a-f]{64}) with \d+ txn prefilled, (\d+) txn from mempool '
    r'\(incl at least (\d+) from extra pool\) and (\d+) txn \((\d+) bytes\) requested')
RX_TXID = re.compile(r'[0-9a-f]{64}')
# ... was not accepted: insufficient fee, rejecting replacement <txid>, less fees than conflicting txs;
#     0.0000001 < 0.0000035
RX_RBF_FEES = re.compile(r'\] ([0-9a-f]{64}) .*?; ([\d.]+) < ([\d.]+)')
RX_MAPSZ = re.compile(r'mapsz (\d+)')


def timestamp(line):
    return line[:27]


def parse_time(t):
    return dt.datetime.fromisoformat(t.rstrip('Z'))


def window_lines(workdir, day, history_days=HISTORY_DAYS):
    """the extract lines from history_days before day to its end, in time order"""
    first_day = dt.date.fromisoformat(day) - dt.timedelta(days=history_days)
    start = f'{first_day}T00:00:00'
    end = f'{dt.date.fromisoformat(day) + dt.timedelta(days=1)}T00:00:00'
    extracts = []
    for path in glob.glob(os.path.join(workdir, 'extract', '*.grep.zst')):
        with open(path[:-len('.grep.zst')] + '.range') as f:
            first, last = (f.read().split('\t') + [''])[:2]
        if first and first < end and last.strip() >= start:
            extracts.append((first, path))
    for _, path in sorted(extracts):
        p = subprocess.Popen(['zstd', '-dcf', path], stdout=subprocess.PIPE, text=True, errors='replace')
        for line in p.stdout:
            if start <= timestamp(line) < end:
                yield line
        if p.wait() != 0:
            raise RuntimeError(f'zstd failed on {path}')


def tx_role(nin, p2a_in, p2a_out, zero_outputs):
    if p2a_in and nin == 1 and zero_outputs:
        return 'sweeper'
    if p2a_in and p2a_out and nin == 1:
        return 'link'
    if p2a_in and p2a_out:
        return 'refill'
    if p2a_in:
        return 'closing'
    if p2a_out:
        return 'root'
    return 'other'


def read_blocks(path):
    """block hash -> {height, txs}, in file order; txid -> info"""
    blocks = collections.OrderedDict()
    txs = {}
    with open(path) as f:
        for line in f:
            (height, block_hash, _, txid, wtxid, vsize, fee, nin, nout, p2a_in, anchor, rune, zero_outputs,
             parent) = line.rstrip('\n').split('\t')
            blocks.setdefault(block_hash, {'height': height, 'txs': []})['txs'].append(txid)
            txs[txid] = dict(height=height, wtxid=wtxid, vsize=int(vsize), fee=int(fee), nin=int(nin),
                             nout=int(nout),
                             anchor=int(anchor) if anchor else None, rune=rune == '1', parent=parent,
                             role=tx_role(int(nin), int(p2a_in), anchor, zero_outputs == '1'))
    return blocks, txs


def is_rune140(info):
    return 139 <= info['vsize'] <= 141 and info['nin'] == 1 and info['nout'] == 2 and info['rune']


def scan_mempool(lines, txs):
    """first pass: each block tx's mempool events, and each block's first header, arrival and reconstruction"""
    events = collections.defaultdict(list)  # txid -> [(time, kind, detail)]
    header, arrival, reconstructed = {}, {}, {}
    first_time = None
    for line in lines:
        if first_time is None:
            first_time = timestamp(line)
        t = timestamp(line)
        if 'Saw new' in line:
            m = RX_HEADER.search(line)
            if m:
                header.setdefault(m.group(2), (t, 'cmpctblock' if m.group(1) else 'headers'))
                continue
        if 'PartiallyDownloadedBlock' in line or 'reconstructed' in line:
            m = RX_ARRIVAL.search(line)
            if m:
                arrival.setdefault(m.group(1), t)
                continue
            m = RX_RECONSTRUCTED.search(line)
            if m:
                reconstructed.setdefault(m.group(1), (t, *map(int, m.groups()[1:])))
                continue
        if m := RX_ACCEPTED.search(line):
            if m.group(1) in txs:
                events[m.group(1)].append((t, 'accepted', m.group(2)))
        elif m := RX_REPLACED.search(line):
            if m.group(1) in txs:
                events[m.group(1)].append((t, 'replaced', m.group(2)))
        elif m := RX_REJECTED.search(line):
            if m.group(1) in txs:
                events[m.group(1)].append((t, 'rejected', m.group(2).strip()))
        elif m := RX_ORPHAN_STORED.search(line):
            if m.group(1) in txs:
                events[m.group(1)].append((t, 'orphan', ''))
        elif m := RX_ORPHAN_REMOVED.search(line):
            if m.group(1) in txs:
                events[m.group(1)].append((t, 'orphan_removed', ''))
    return events, header, arrival, reconstructed, first_time


def missing_status(txid, arrival, events, txs):
    """(in pool?, why missing, txid of its replacement)"""
    state, why, replaced_by = 'none', 'never seen in window', ''
    for t, kind, detail in events.get(txid, ()):
        if t >= arrival:
            break
        if kind == 'accepted':
            state, why, replaced_by = 'pool', '', ''
            if detail != txs[txid]['wtxid']:
                state, why = 'missing', 'in mempool with a different wtxid'
        elif kind == 'replaced':
            state, why, replaced_by = 'missing', 'replaced', detail
        elif kind == 'rejected' and state != 'pool':
            state, why, replaced_by = 'missing', 'rejected: ' + detail, ''
        elif kind == 'orphan' and state != 'pool':
            state, why = 'missing', 'orphan'
    return state == 'pool', why, replaced_by


def scan_heads(lines, day, heads, watched):
    """second pass: the fee each chain head lost an RBF against, the day's orphans stored, and for each watched tx
    when a peer first sent it and its orphanage events"""
    lost_to_fee = {}
    orphans = collections.Counter()
    first_sent = {}
    events = collections.defaultdict(list)  # txid -> [(time, kind)]
    for line in lines:
        t = timestamp(line)
        m = RX_TXID.search(line, 28)
        if m and m.group(0) in watched:
            txid = m.group(0)
            sent = False
            if 'AcceptToMemoryPool' in line:
                kind, sent = 'accepted', True
            elif 'was not accepted' in line:
                kind, sent = 'rejected', True
            elif 'stored orphan' in line:
                kind = 'orphan'
            elif 'removed orphan' in line:
                kind = 'orphan_removed'
            elif 'replacing mempool tx' in line:
                kind = 'replaced'
            else:
                kind = None
            if kind:
                if sent:
                    first_sent.setdefault(txid, t)
                events[txid].append((t, kind))
        if 'stored orphan' in line and line.startswith(day):
            m = RX_MAPSZ.search(line)
            orphans['stored'] += 1
            orphans['full'] += bool(m and int(m.group(1)) >= ORPHANAGE_LIMIT)
            continue
        if 'was not accepted: insufficient fee' not in line:
            continue
        m = RX_RBF_FEES.search(line)
        if m and m.group(1) in heads:
            lost_to_fee.setdefault(m.group(1), round(float(m.group(3)) * 1e8))
    return lost_to_fee, orphans, first_sent, events


def orphanage_state(events, arrival):
    state = 'never seen'
    for t, kind in events:
        if t >= arrival:
            break
        if kind == 'accepted':
            state = 'in mempool'
        elif kind == 'orphan':
            state = 'in orphanage'
        elif kind == 'orphan_removed':
            state = 'dropped from orphanage' if state == 'in orphanage' else state
        elif kind == 'replaced':
            state = 'replaced'
        elif kind == 'rejected':
            state = state if state in ('in mempool', 'in orphanage') else 'rejected'
    return state


def write_csv(path, header, rows):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main(workdir, day):
    blocks, txs = read_blocks(os.path.join(workdir, 'blocks', day + '.tsv'))
    events, header, arrival, reconstructed, first_time = scan_mempool(window_lines(workdir, day), txs)

    # every block that arrived inside the window, with the txs it lacked
    missing_rows = []
    day_blocks = []  # reconstructed blocks of the day: (hash, block, [missing txid])
    for block_hash, block in blocks.items():
        t = arrival.get(block_hash)
        if not t or t < first_time:
            continue
        missing = []
        for txid in block['txs']:
            in_pool, why, replaced_by = missing_status(txid, t, events, txs)
            if not in_pool:
                missing.append(txid)
                if replaced_by in txs:
                    by_role = txs[replaced_by]['role']
                else:
                    by_role = 'not in these blocks' if replaced_by else ''
                info = txs[txid]
                missing_rows.append([block['height'], t, txid, info['role'], info['vsize'], info['fee'], why,
                                     replaced_by, by_role])
        if t[:10] == day and block_hash in reconstructed:
            day_blocks.append((block_hash, block, missing))

    why_missing = {row[2]: row[6] for row in missing_rows}
    day_missing = {txid for _, _, missing in day_blocks for txid in missing}
    # the first missing tx of each P2A chain, when the node refused it for its fee
    heads = {txid for txid in day_missing
             if txs[txid]['role'] != 'other' and txs[txid]['parent'] not in day_missing
             and why_missing[txid].startswith('rejected: insufficient')}
    arrived_today = [row for row in missing_rows if row[1][:10] == day]
    watched = {row[2] for row in arrived_today}
    lost_to_fee, orphans, first_sent, orphan_events = scan_heads(window_lines(workdir, day), day, heads, watched)

    def chain_head(txid):
        for _ in range(200):
            if txs[txid]['parent'] not in day_missing:
                break
            txid = txs[txid]['parent']
        return txid

    def cause(txid):
        info = txs[txid]
        if info['role'] == 'other':
            return 'rune140' if is_rune140(info) else 'other'
        head = chain_head(txid)
        parent = txs.get(txs[head]['parent'])
        if head in lost_to_fee:
            if txs[head]['role'] == 'sweeper':
                return 'p2a_chain_won_here'
            if txs[head]['role'] == 'root':
                return 'p2a_root_conflict'
            if parent and parent['anchor'] is not None and lost_to_fee[head] == parent['anchor']:
                return 'p2a_sweeper_won_here'
        return 'p2a_other'

    out = os.path.join(workdir, 'days', day)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'missing.tsv'), 'w') as f:
        f.write('height\tarrival\ttxid\trole\tvsize\tfee\twhy\treplaced_by\treplaced_by_role\n')
        f.writelines('\t'.join(map(str, row)) + '\n' for row in missing_rows)

    cause_txs, cause_vbytes = collections.Counter(), collections.Counter()
    block_rows = []
    for block_hash, block, missing in day_blocks:
        per_cause = collections.Counter(cause(txid) for txid in missing)
        for txid in missing:
            cause_txs[cause(txid)] += 1
            cause_vbytes[cause(txid)] += txs[txid]['vsize']
        done, from_mempool, extra, requested, requested_bytes = reconstructed[block_hash]
        seen, via = header.get(block_hash, ('', ''))
        ms = round((parse_time(done) - parse_time(seen)).total_seconds() * 1000) if seen else ''
        block_rows.append([day, block['height'], arrival[block_hash], ms, requested, extra, requested_bytes,
                           len(missing), *(per_cause[c] for c in CAUSES), via])
    write_csv(os.path.join(out, 'blocks.csv'),
              ['day', 'height', 'arrival', 'reconstruct_ms', 'requested', 'extra_pool', 'requested_bytes',
               'calc_missing', *CAUSES, 'header_via'], block_rows)
    write_csv(os.path.join(out, 'causes.csv'), ['day', 'cause', 'txs', 'vbytes'],
              ([day, c, cause_txs[c], cause_vbytes[c]] for c in CAUSES))

    kinds = collections.Counter()
    day_heights = {block['height'] for _, block, _ in day_blocks}
    for info in txs.values():
        if info['height'] in day_heights:
            if info['anchor'] is not None or info['parent']:
                kinds['p2a'] += 1
            else:
                kinds['rune140' if is_rune140(info) else 'other'] += 1
    write_csv(os.path.join(out, 'block_types.csv'), ['day', 'type', 'txs'],
              ([day, k, kinds[k]] for k in ('p2a', 'rune140', 'other')))

    head_kinds = collections.Counter()
    for head in heads:
        parent = txs.get(txs[head]['parent'])
        anchor = parent['anchor'] if parent else None
        if head not in lost_to_fee:
            had = 'unknown'
        elif txs[head]['role'] == 'root':
            had = 'another tx spending one of its inputs'
        elif anchor is None:
            had = 'unknown'
        elif lost_to_fee[head] == anchor:
            had = 'a tx paying the whole anchor as fee (sweeper)'
        else:
            had = 'other txs with a different fee'
        head_kinds[(txs[head]['role'], had)] += 1
    write_csv(os.path.join(out, 'chain_heads.csv'), ['day', 'head_role', 'our_mempool_had', 'heads'],
              ([day, role, had, n]
               for (role, had), n in sorted(head_kinds.items(), key=lambda kv: (-kv[1], kv[0]))))

    # how long the missing chain txs that sat in the orphanage when their block came had been around
    arrival_of = {row[2]: row[1] for row in missing_rows}
    waits = sorted((parse_time(arrival_of[row[2]]) - parse_time(first_sent[row[2]])).total_seconds() / 60
                   for row in arrived_today
                   if row[3] != 'other' and row[2] in first_sent
                   and orphanage_state(orphan_events.get(row[2], ()), row[1]) == 'in orphanage')
    quartile = lambda q: round(waits[int(len(waits) * q)], 1) if waits else ''
    write_csv(os.path.join(out, 'orphanage.csv'),
              ['day', 'orphans_stored', 'stored_with_orphanage_full', 'chain_txs_in_orphanage_at_block',
               'wait_p25_min', 'wait_median_min', 'wait_p75_min'],
              [[day, orphans['stored'], orphans['full'], len(waits),
                quartile(0.25), quartile(0.5), quartile(0.75)]])

    validated = sum(1 for row in block_rows if row_validated(row))
    print(json.dumps(dict(day=day, window_starts=first_time, blocks=len(block_rows), validated=validated,
                          round_trips=sum(1 for row in block_rows if row[4] > 0),
                          missing=sum(cause_txs.values()))))


def row_validated(row):
    """the rebuilt missing count lands within what the log says the block requested and took from the extra pool"""
    requested, extra, calc = row[4], row[5], row[7]
    return requested <= calc <= requested + extra or abs(calc - requested) <= max(2, 0.1 * requested)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
