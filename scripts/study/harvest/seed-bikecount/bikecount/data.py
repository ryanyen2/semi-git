"""Reading the sensor file off disk."""
import csv
import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    """One hour at the Fremont Bridge."""
    when: datetime.datetime
    total: int
    east: int
    west: int


def load_readings(path):
    """Read the counter csv. Hours where either sensor reported nothing are skipped,
    because a total with one side missing is not comparable with the rest."""
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
