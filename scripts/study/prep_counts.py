"""Turn the two raw open-data downloads into one small csv each, same schema both times.

Fremont Bridge (Seattle) counts bicycles on two sidewalks. Bourke Street Mall
(Melbourne) counts pedestrians on two sides of one street. Different cities and
different things being counted, but the same shape: one location, two sensors,
one row per hour. Writing both out with the same column names is what lets the
two study projects run the same analysis code.

    python3 scripts/study/prep_counts.py fremont  <raw.csv> <out.csv>
    python3 scripts/study/prep_counts.py melbourne <raw.csv> <out.csv>
"""
import collections
import csv
import datetime
import sys

MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}


def fremont(src, out):
    """Already one row per hour with both sensors on it. Just rename the columns."""
    with open(src, newline="") as fh, open(out, "w", newline="") as w:
        r = csv.DictReader(fh)
        out_w = csv.writer(w)
        out_w.writerow(["timestamp", "total", "east_sidewalk", "west_sidewalk"])
        n = 0
        for row in r:
            stamp = row["Date"][:19]
            east = row["Fremont Bridge East Sidewalk"].strip()
            west = row["Fremont Bridge West Sidewalk"].strip()
            total = row["Fremont Bridge Total"].strip()
            out_w.writerow([stamp, total, east, west])
            n += 1
    return n


def melbourne(src, out, sensor_a="Spencer St-Collins St (North)", sensor_b="Spencer St-Collins St (South)"):
    """One row per sensor per hour, so the two sides have to be pivoted onto one row.
    Only the two Spencer St-Collins St sensors are kept; the file has dozens of others.

    That pair rather than a busier one because it is the only one that behaves like
    the Fremont Bridge sidewalks. It sits on the walk between Southern Cross Station
    and the offices on Collins St, so the two sides carry opposite halves of the
    commute: on a 2019 weekday the south side peaks at 8am and the north side at
    5pm, and the two added together peak at 5pm. Dropping either one moves the
    headline busiest hour, which is the same shape of change the Seattle data
    supports. The shopping-street sensors do not: Bourke Street Mall peaks at 1pm
    on both sides and dropping one changes nothing anybody would notice.
    """
    hours = collections.defaultdict(dict)
    with open(src, newline="") as fh:
        for row in csv.DictReader(fh):
            name = row["Sensor_Name"]
            if name not in (sensor_a, sensor_b):
                continue
            stamp = datetime.datetime(
                int(row["Year"]), MONTHS[row["Month"]], int(row["Mdate"]), int(row["Time"]))
            hours[stamp][name] = row["Hourly_Counts"].strip()

    with open(out, "w", newline="") as w:
        out_w = csv.writer(w)
        out_w.writerow(["timestamp", "total", "north_side", "south_side"])
        n = 0
        for stamp in sorted(hours):
            a = hours[stamp].get(sensor_a, "")
            b = hours[stamp].get(sensor_b, "")
            total = "" if a == "" or b == "" else str(int(a) + int(b))
            out_w.writerow([stamp.isoformat(), total, a, b])
            n += 1
    return n


if __name__ == "__main__":
    which, src, out = sys.argv[1], sys.argv[2], sys.argv[3]
    count = {"fremont": fremont, "melbourne": melbourne}[which](src, out)
    print(f"wrote {count} rows to {out}")
