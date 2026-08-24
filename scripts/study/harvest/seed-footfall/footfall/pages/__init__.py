"""Pages, one module each.

A page module sets `PATH`, `TITLE` and `ORDER`, and defines `render(readings)`
returning the body html. `discover()` finds them at import time, so adding a page
means adding a file here and nothing else: no route to register by hand, no nav
list to edit. That keeps two pages from having to share a function neither of them
is about.
"""
import importlib
import pkgutil

STYLE = """
body { font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 0; color: #1a1a1a; background: #fbfbfa; }
header { padding: 20px 32px; border-bottom: 1px solid #e3e3e0; background: #fff; }
h1 { font-size: 17px; margin: 0; font-weight: 600; }
nav a { margin-right: 18px; color: #555; text-decoration: none; font-size: 14px; }
main { padding: 28px 32px; max-width: 900px; }
.big { font-size: 40px; font-weight: 600; letter-spacing: -1px; }
.label { color: #777; font-size: 13px; text-transform: uppercase; letter-spacing: .06em; }
table { border-collapse: collapse; font-size: 14px; }
td, th { padding: 5px 14px 5px 0; text-align: left; }
th { color: #777; font-weight: 500; border-bottom: 1px solid #e3e3e0; }
"""


def discover():
    """Every page module in this package, in the order they should appear in the nav."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "PATH") and hasattr(mod, "render"):
            found.append(mod)
    return sorted(found, key=lambda m: getattr(m, "ORDER", 100))


def shell(title, body):
    """The html around every page. The nav is built from whatever discover() finds."""
    links = "".join(f'<a href="{m.PATH}">{m.TITLE}</a>' for m in discover())
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title><style>{STYLE}</style></head>
<body>
<header><h1>Spencer Street pedestrian counter</h1>
<nav>{links}</nav></header>
<main>{body}</main>
</body></html>"""
