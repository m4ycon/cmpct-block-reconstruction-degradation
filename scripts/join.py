"""Joins the per-log counts of peerhour.awk into counts per peer group.

usage: join.py <counts_dir> <dest_dir>
Reads <counts_dir>/<index>.cnt and .ver in index order, which is time order. A peer id keeps the group of its
version message across files, until a bitcoind start (segment > 0) forgets every peer id.
Writes full_daily_msgs.csv, full_hourly_msgs.csv and full_daily_peers.csv to <dest_dir>; progress and the tally
of user agents outside "other" go to stderr.
"""
import collections
import csv
import os
import re
import sys

VERSION_RX = re.compile(r'receive version message: (.*): version (\d+), .*peer=(\d+)')


def peer_group(ua, protocol):
    if ua.startswith('/p2aburn'):
        return 'p2aburn'
    if ua.startswith('/txbroadcast'):
        return 'txbroadcast'
    if ua == '/Sat0shi:31.0.0/' or (ua == '/Satoshi:31.0.0/' and protocol == '70015'):
        return 'fleet'
    return 'other'


def read_versions(path):
    """segment -> [(day, ua, protocol, peer id)]"""
    by_segment = collections.defaultdict(list)
    with open(path, errors='replace') as f:
        for line in f:
            segment, text = line.rstrip('\n').split('\t', 1)
            m = VERSION_RX.search(text)
            if m:
                by_segment[int(segment)].append((text[:10], *m.groups()))
    return by_segment


def read_counts(path):
    """segment -> [(hour, peer id, msg, count, bytes)]"""
    by_segment = collections.defaultdict(list)
    with open(path, errors='replace') as f:
        for line in f:
            segment, hour, peer, msg, count, nbytes = line.rstrip('\n').split('\t')
            by_segment[int(segment)].append((hour, peer[len('peer='):], msg, int(count), int(nbytes or 0)))
    return by_segment


def write_csv(path, header, rows):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main(counts_dir, dest):
    daily = collections.defaultdict(lambda: [0, 0])  # (day, group, msg) -> [count, bytes]
    hourly = collections.defaultdict(lambda: [0, 0])  # (hour, group, msg) -> [count, bytes]
    day_peers = collections.defaultdict(set)  # (day, group) -> peer ids
    new_connections = collections.Counter()  # (day, group) -> version messages
    user_agents = collections.Counter()  # (group, ua, protocol) -> version messages
    peers = {}  # peer id -> group
    indexes = sorted(name[:-len('.cnt')] for name in os.listdir(counts_dir) if name.endswith('.cnt'))
    for index in indexes:
        versions = read_versions(os.path.join(counts_dir, index + '.ver'))
        counts = read_counts(os.path.join(counts_dir, index + '.cnt'))
        for segment in sorted(set(versions) | set(counts)):
            if segment > 0:
                peers = {}
            for day, ua, protocol, peer in versions[segment]:
                group = peer_group(ua, protocol)
                peers[peer] = group
                if group != 'other':
                    new_connections[(day, group)] += 1
                    user_agents[(group, ua, protocol)] += 1
            for hour, peer, msg, count, nbytes in counts[segment]:
                group = peers.get(peer, 'unknown')
                if msg == 'version' and group == 'unknown':
                    continue
                day = hour[:10]
                for totals in (daily[(day, group, msg)], hourly[(hour, group, msg)]):
                    totals[0] += count
                    totals[1] += nbytes
                day_peers[(day, group)].add(peer)
        print(index, 'ok', file=sys.stderr)

    os.makedirs(dest, exist_ok=True)
    write_csv(os.path.join(dest, 'full_daily_msgs.csv'), ['day', 'group', 'msg', 'count', 'bytes'],
              ([*key, *daily[key]] for key in sorted(daily)))
    write_csv(os.path.join(dest, 'full_hourly_msgs.csv'), ['hour', 'group', 'msg', 'count', 'bytes'],
              ([hour + ':00:00Z', group, msg, *hourly[(hour, group, msg)]] for hour, group, msg in sorted(hourly)))
    write_csv(os.path.join(dest, 'full_daily_peers.csv'), ['day', 'group', 'distinct_peers', 'new_connections'],
              ([day, group, len(day_peers[(day, group)]), new_connections[(day, group)]]
               for day, group in sorted(day_peers)))
    for key, n in sorted(user_agents.items(), key=lambda kv: -kv[1]):
        print('UA', key, n, file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
