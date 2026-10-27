"""Turns join.py's output into the CSVs the notebook reads.

usage: daily_csv.py <join_dir> <dest_dir>
Writes to <dest_dir>:
- full_daily_msgs.csv: one row per day and group (unknown only on days it has any).
- full_hourly_tx.csv: txs received and getdatas sent per hour and group.
- daily_metrics.csv: per day, the share of compact blocks reconstructed without requesting txs, the blocks
  reconstructed, and the txs rejected for missing or spent inputs.
- events.csv: when each named group arrives and stops. Arrives: its first version message, if the logs have
  every one of the HOURS_SEEN_BEFORE_ARRIVAL hours before it, since a group first seen right after a gap may
  have arrived inside it. Stops: the last message one of its peers sent us, if that is more than STILL_ACTIVE
  before the end of the data.
Prints the first day, the last day and the number of days.
"""
import collections
import csv
import datetime as dt
import sys

GROUPS = ['p2aburn', 'fleet', 'txbroadcast', 'other', 'unknown']
NAMED_GROUPS = ['p2aburn', 'fleet', 'txbroadcast']
COLUMNS = ['tx', 'messages', 'tx_bytes', 'getdata_sent', 'new_connections', 'distinct_peers']
STILL_ACTIVE = dt.timedelta(hours=24)
HOURS_SEEN_BEFORE_ARRIVAL = 24


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


def daily_metrics(join_dir, days):
    """[day, no-request share, blocks reconstructed, rejected for missing inputs] for each day"""
    metrics = collections.Counter()
    for row in read_rows(join_dir + '/daily_metrics.csv'):
        metrics[(row['day'], row['metric'])] = int(row['count'])
    for day in sorted(set(days) | {day for day, _ in metrics}):
        blocks = metrics[(day, 'blocks_reconstructed')]
        share = round(metrics[(day, 'reconstructed_without_request')] / blocks, 4) if blocks else ''
        yield [day, share, blocks, metrics[(day, 'rejected_inputs_missing')]]


def logged_hours(join_dir):
    """the hours with at least one message in the logs"""
    return {dt.datetime.fromisoformat(row['hour']) for row in read_rows(join_dir + '/full_hourly_msgs.csv')}


def logs_cover_before(hours, time):
    hour = time.replace(minute=0, second=0, microsecond=0)
    return all(hour - dt.timedelta(hours=n) in hours for n in range(1, HOURS_SEEN_BEFORE_ARRIVAL + 1))


def events(join_dir):
    """[time, group, label], in time order"""
    times = {row['group']: row for row in read_rows(join_dir + '/group_times.csv')}
    hours = logged_hours(join_dir)
    end_of_data = max(dt.datetime.fromisoformat(row['last_seen']) for row in times.values() if row['last_seen'])
    found = []
    for group in NAMED_GROUPS:
        row = times.get(group)
        if not row:
            continue
        if row['first_version'] and logs_cover_before(hours, dt.datetime.fromisoformat(row['first_version'])):
            found.append([row['first_version'], group, f'{group} arrives'])
        if row['last_seen'] and end_of_data - dt.datetime.fromisoformat(row['last_seen']) > STILL_ACTIVE:
            found.append([row['last_seen'], group, f'{group} stops'])
    return sorted(found)


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

    with open(dest + '/daily_metrics.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['day', 'reconstruction_no_request_share', 'blocks_reconstructed', 'rejected_inputs_missing'])
        w.writerows(daily_metrics(join_dir, days))

    with open(dest + '/events.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['time', 'group', 'label'])
        w.writerows(events(join_dir))

    print(days[0], days[-1], len(days))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
