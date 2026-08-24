"""Reading the sensor file off disk."""
import csv
import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class Reading:
    """One hour at the Spencer Street crossing."""
    when: datetime.datetime
    total: int
    north: int
    south: int


def load_readings(path):
    """Read the counter csv. Hours where either sensor reported nothing are skipped,
    because a total with one side missing is not comparable with the rest."""
    readings = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if not row["total"] or not row["north_side"] or not row["south_side"]:
                continue
            readings.append(Reading(
                when=datetime.datetime.fromisoformat(row["timestamp"]),
                total=int(float(row["total"])),
                north=int(float(row["north_side"])),
                south=int(float(row["south_side"])),
            ))
    return readings
