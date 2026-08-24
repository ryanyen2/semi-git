# bikecount

A small dashboard over the Fremont Bridge bicycle counter in Melbourne.

The city has sensors on both sides of the crossing between Southern Cross station
and the Collins Street offices, counting people every hour since 2013. The city
publishes the file and we use it for the cycling team's quarterly report.

Run it:

    python3 -m bikecount.server

Then open http://localhost:8000.

The data file is `data/counts.csv`. Columns are the hour, the total across both
sides, and the two sides separately.

## Adding a page

Put a module in `bikecount/pages/`. Give it `PATH`, `TITLE`, `ORDER` and a
`render(readings)` that returns the body html, and wrap it with `pages.shell`.
That is all: the nav and the routing both come from whatever
`pages.discover()` finds, so there is no list to keep in step and two pages never
have to share a function. `check.py` walks the same list, so a new page is covered
by the smoke check for free.
