"""Reading the counter file off disk."""
import csv
import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    """One hour on the bridge."""
    when: datetime.datetime
    total: int
    east: int
    west: int


def load_readings(path):
    """Read the counter csv. Hours where the counter reported nothing are skipped."""
    readings = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row["total"] or not row["east_sidewalk"] or not row["west_sidewalk"]:
                continue
            readings.append(Reading(
                when=datetime.datetime.fromisoformat(row["timestamp"]),
                total=int(float(row["total"])),
                east=int(float(row["east_sidewalk"])),
                west=int(float(row["west_sidewalk"])),
            ))
    return readings


def daily_totals(readings):
    """Total per calendar day, oldest first."""
    days = {}
    for r in readings:
        day = r.when.date()
        days[day] = days.get(day, 0) + r.total
    return sorted(days.items())
