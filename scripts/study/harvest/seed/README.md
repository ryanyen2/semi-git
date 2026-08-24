# bikecount

A small dashboard over the Fremont Bridge bicycle counter in Seattle.

The counter sits on the Fremont Bridge and counts bikes crossing on each
sidewalk, one row per hour, going back to October 2012. The city publishes it
and we use it for the quarterly cycling report.

Run it:

    python3 -m bikecount.server

Then open http://localhost:8000.

The data file is `data/counts.csv`. Columns are the hour, the total across both
sidewalks, and the two sidewalks separately.
