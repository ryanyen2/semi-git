"""The landing page: the busiest day on record and the last fortnight."""
from footfall import charts, metrics
from footfall.pages import shell

PATH = "/"
TITLE = "Overview"
ORDER = 0


def render(readings):
    days = metrics.daily_totals(readings)
    day, count = max(days, key=lambda kv: kv[1])
    recent = days[-14:]
    rows = "".join(f"<tr><td>{d}</td><td>{c:,}</td></tr>" for d, c in recent[::-1])
    return shell("Overview", f"""
<p class="label">Busiest day on record</p>
<p class="big">{count:,}</p>
<p>{day:%A %d %B %Y}</p>
<p class="label" style="margin-top:32px">Last 14 days</p>
{charts.bar_chart(recent, label=lambda d: d.strftime("%d %b"))}
<table><tr><th>Day</th><th>People</th></tr>{rows}</table>
<p style="color:#777;margin-top:32px">{len(readings):,} hours of readings.</p>
""")
