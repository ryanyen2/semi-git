"""Turning readings into the numbers the pages show."""


def daily_totals(readings):
    """Total per calendar day, oldest first."""
    days = {}
    for r in readings:
        day = r.when.date()
        days[day] = days.get(day, 0) + r.total
    return sorted(days.items())


def busiest_day(readings):
    """The day with the most people, and its count."""
    return max(daily_totals(readings), key=lambda kv: kv[1])
