"""Turns join.py's per-message counts into the CSVs the notebook reads.

usage: daily_csv.py <join_dir> <dest_dir>
Writes <dest_dir>/full_daily_msgs.csv, one row per day and group (unknown only on days it has any), and
<dest_dir>/full_hourly_tx.csv, txs received and getdatas sent per hour and group.
Prints the first day, the last day and the number of days.
"""
import collections
import csv
import sys

GROUPS = ['p2aburn', 'fleet', 'txbroadcast', 'other', 'unknown']
COLUMNS = ['tx', 'messages', 'tx_bytes', 'getdata_sent', 'new_connections', 'distinct_peers']


def read_rows(path):
    with open(path) as f:
        yield from csv.DictReader(f)


def daily_totals(join_dir):
    """(day, group) -> Counter over COLUMNS; "messages" counts every type received, getdatas sent apart"""
    totals = collections.defaultdict(collections.Counter)
    for row in read_rows(join_dir + '/full_daily_msgs.csv'):
        t = totals[(row['day'], row['group'])]
        count = int(row['count'])
        if row['msg'] == 'getdata_sent':
            t['getdata_sent'] += count
            continue
        t['messages'] += count
        if row['msg'] == 'tx':
            t['tx'] += count
            t['tx_bytes'] += int(row['bytes'])
    for row in read_rows(join_dir + '/full_daily_peers.csv'):
        t = totals[(row['day'], row['group'])]
        t['distinct_peers'] = int(row['distinct_peers'])
        t['new_connections'] = int(row['new_connections'])
    return totals


def hourly_tx(join_dir):
    """(hour, group) -> [tx, getdata_sent]"""
    totals = collections.defaultdict(lambda: [0, 0])
    for row in read_rows(join_dir + '/full_hourly_msgs.csv'):
        key = (row['hour'], row['group'])
        if row['msg'] == 'tx':
            totals[key][0] += int(row['count'])
        elif row['msg'] == 'getdata_sent':
            totals[key][1] += int(row['count'])
    return totals


def main(join_dir, dest):
    daily = daily_totals(join_dir)
    days = sorted({day for day, _ in daily})
    with open(dest + '/full_daily_msgs.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['day', 'group', *COLUMNS])
        for day in days:
            for group in GROUPS:
                t = daily.get((day, group), collections.Counter())
                if group == 'unknown' and not t:
                    continue
                w.writerow([day, group, *(t[c] for c in COLUMNS)])

    with open(dest + '/full_hourly_tx.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['hour', 'day', 'group', 'tx', 'getdata_sent'])
        for (hour, group), (tx, getdata_sent) in sorted(hourly_tx(join_dir).items()):
            w.writerow([hour, hour[:10], group, tx, getdata_sent])

    print(days[0], days[-1], len(days))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
