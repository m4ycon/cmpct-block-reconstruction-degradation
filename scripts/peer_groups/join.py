"""Joins the per-log counts of peerhour.awk into counts per peer group.

usage: join.py <counts_dir> <dest_dir>
Reads <counts_dir>/<index>.cnt, .ver, .day and .seen in index order, which is time order. A peer id keeps the
group of its version message across files, until a bitcoind start (segment > 0) forgets every peer id.
Writes to <dest_dir>: full_daily_msgs.csv, full_hourly_msgs.csv and full_daily_peers.csv; daily_metrics.csv, the
.day metrics summed per day; group_times.csv, each group's first version message and the last time one of its
peers sent us a message. Progress and the tally of user agents outside "other" go to stderr.
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
    """segment -> [(time, ua, protocol, peer id)]"""
    by_segment = collections.defaultdict(list)
    with open(path, errors='replace') as f:
        for line in f:
            segment, text = line.rstrip('\n').split('\t', 1)
            m = VERSION_RX.search(text)
            if m:
                time = text.split(' ', 1)[0]
                by_segment[int(segment)].append((time, *m.groups()))
    return by_segment


def read_counts(path):
    """segment -> [(hour, peer id, msg, count, bytes)]"""
    by_segment = collections.defaultdict(list)
    with open(path, errors='replace') as f:
        for line in f:
            segment, hour, peer, msg, count, nbytes = line.rstrip('\n').split('\t')
            by_segment[int(segment)].append((hour, peer[len('peer='):], msg, int(count), int(nbytes or 0)))
    return by_segment


def read_last_seen(path):
    """segment -> [(peer id, time)]"""
    by_segment = collections.defaultdict(list)
    with open(path) as f:
        for line in f:
            segment, peer, time = line.rstrip('\n').split('\t')
            by_segment[int(segment)].append((peer[len('peer='):], time))
    return by_segment


def read_daily_metrics(path):
    """[(day, metric, count)]"""
    rows = []
    with open(path) as f:
        for line in f:
            day, metric, count = line.rstrip('\n').split('\t')
            rows.append((day, metric, int(count)))
    return rows


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
    metrics = collections.Counter()  # (day, metric) -> count
    first_version = {}  # group -> time
    last_seen = {}  # group -> time
    peers = {}  # peer id -> group
    indexes = sorted(name[:-len('.cnt')] for name in os.listdir(counts_dir) if name.endswith('.cnt'))
    for index in indexes:
        versions = read_versions(os.path.join(counts_dir, index + '.ver'))
        counts = read_counts(os.path.join(counts_dir, index + '.cnt'))
        seen = read_last_seen(os.path.join(counts_dir, index + '.seen'))
        for day, metric, count in read_daily_metrics(os.path.join(counts_dir, index + '.day')):
            metrics[(day, metric)] += count
        for segment in sorted(set(versions) | set(counts)):
            if segment > 0:
                peers = {}
            for time, ua, protocol, peer in versions[segment]:
                group = peer_group(ua, protocol)
                peers[peer] = group
                first_version.setdefault(group, time)
                if group != 'other':
                    new_connections[(time[:10], group)] += 1
                    user_agents[(group, ua, protocol)] += 1
            for peer, time in seen[segment]:
                group = peers.get(peer, 'unknown')
                last_seen[group] = max(last_seen.get(group, time), time)
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
    write_csv(os.path.join(dest, 'daily_metrics.csv'), ['day', 'metric', 'count'],
              ([day, metric, metrics[(day, metric)]] for day, metric in sorted(metrics)))
    write_csv(os.path.join(dest, 'group_times.csv'), ['group', 'first_version', 'last_seen'],
              ([group, first_version.get(group, ''), last_seen.get(group, '')]
               for group in sorted(first_version.keys() | last_seen.keys())))
    for key, n in sorted(user_agents.items(), key=lambda kv: -kv[1]):
        print('UA', key, n, file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
