"""Smoke check. Renders every page the app knows about and fails if one blows up.

It walks whatever `pages.discover()` returns, so a page added later is covered
without touching this file.

    python3 check.py
"""
import sys

from bikecount import data, pages, server


def main():
    readings = data.load_readings(server.DATA)
    if len(readings) < 1000:
        print(f"only {len(readings)} readings loaded, the data file looks wrong")
        return 1

    found = pages.discover()
    if not found:
        print("no pages found")
        return 1

    for module in found:
        html = module.render(readings)
        if "<html" not in html or len(html) < 200:
            print(f"{module.PATH} came back empty")
            return 1

    names = ", ".join(m.PATH for m in found)
    print(f"ok: {len(readings):,} readings, {len(found)} page(s) render: {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
