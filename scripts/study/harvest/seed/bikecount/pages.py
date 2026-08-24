"""The html. Every page is a string built here."""

STYLE = """
body { font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0; color: #1a1a1a; background: #fbfbfa; }
header { padding: 20px 32px; border-bottom: 1px solid #e3e3e0; background: #fff; }
h1 { font-size: 17px; margin: 0; font-weight: 600; }
nav a { margin-right: 18px; color: #555; text-decoration: none; font-size: 14px; }
nav a:hover { color: #000; }
main { padding: 28px 32px; max-width: 900px; }
.big { font-size: 40px; font-weight: 600; letter-spacing: -1px; }
.label { color: #777; font-size: 13px; text-transform: uppercase; letter-spacing: .06em; }
table { border-collapse: collapse; font-size: 14px; }
td, th { padding: 5px 14px 5px 0; text-align: left; }
th { color: #777; font-weight: 500; border-bottom: 1px solid #e3e3e0; }
"""


def page(title, body):
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title><style>{STYLE}</style></head>
<body>
<header><h1>Fremont Bridge bicycle counter</h1>
<nav><a href="/">Overview</a></nav></header>
<main>{body}</main>
</body></html>"""


def render_overview(readings, days):
    """Landing page. How many hours we have, and the busiest day in the file."""
    busiest_day, busiest_count = max(days, key=lambda kv: kv[1])
    rows = "".join(
        f"<tr><td>{day}</td><td>{count:,}</td></tr>" for day, count in days[-14:][::-1])
    return page("Overview", f"""
<p class="label">Busiest day on record</p>
<p class="big">{busiest_count:,}</p>
<p>{busiest_day:%A %d %B %Y}</p>
<p class="label" style="margin-top:32px">Last 14 days</p>
<table><tr><th>Day</th><th>Crossings</th></tr>{rows}</table>
<p style="color:#777;margin-top:32px">{len(readings):,} hours of readings.</p>
""")
