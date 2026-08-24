# The project: bikecount

Nothing is timed on this page. Read it at whatever pace you want and ask anything you like before you continue. You do not have to memorise any of it. The requests tell you what needs to change, and this is here so that finding your way around is not the first thing you have to do under a clock.


You are taking over **bikecount** from Dana Whitfield, who has left the transport data team.

## What it is for

There is a counter on the Fremont Bridge in Seattle with a sensor on each sidewalk. It counts bikes crossing, one number per hour, going back to 2012. The city publishes the file.

bikecount reads that file and puts it on a web page. Open it in a browser and you get the busiest day on record, a chart of what time of day people ride, totals by month and by year, and a comparison of the two sidewalks. There is no login and no database. It reads the csv off disk every time you load a page.

Three people use it. Its numbers go into the cycling team's quarterly report.

## How it was built

Dana built it over six weeks, mostly by describing what she wanted to an AI assistant and checking the result. Each piece of work is one afternoon's job, and the history says what each one was for.

## What is in it

- **Hour of day.** What time people ride, weekdays and weekends apart.
- **By month and by year.** Totals over time, and a table for the front of the report.
- **East against west.** Whether the two sidewalks are balanced.
- **Quiet days.** A list of days that are nothing like a normal day, such as the February 2019 snowstorm and Christmas. Dana started keeping it because those days kept being read as real drops in cycling.
- **A csv download**, so people stop asking for the numbers by email.

## How to run it

    python3 -m bikecount.server

Then open http://localhost:8000. To check nothing is broken:

    python3 check.py

It renders every page and fails loudly if one of them throws.

## Its condition

It works. The smoke check passes. It has never had a second maintainer.

