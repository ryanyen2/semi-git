"""Smoke check. Renders every page the server knows about and fails if one blows up.

Run it before you save anything:

    python3 check.py
"""
import sys

from bikecount import counts, pages, server


def main():
    readings = counts.load_readings(server.DATA)
    if len(readings) < 1000:
        print(f"only {len(readings)} readings loaded, the data file looks wrong")
        return 1

    days = counts.daily_totals(readings)
    html = pages.render_overview(readings, days)
    if "<html" not in html or len(html) < 200:
        print("the overview page came back empty")
        return 1

    print(f"ok: {len(readings):,} readings, {len(days):,} days, overview renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
