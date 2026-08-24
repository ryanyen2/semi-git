# The project: footfall

Nothing is timed on this page. Read it at whatever pace you want and ask anything you like before you continue. You do not have to memorise any of it. The requests tell you what needs to change, and this is here so that finding your way around is not the first thing you have to do under a clock.


You are taking over **footfall** from Dana Whitfield, who has left the transport data team.

## What it is for

The city has a sensor on each side of the crossing between Southern Cross station and the Collins Street offices in Melbourne. They count people walking past, one number per hour, going back to 2013. The council publishes the file.

footfall reads that file and puts it on a web page. Open it in a browser and you get the busiest day on record, a chart of what time of day people walk past, totals by month and by year, and a comparison of the two sides. There is no login and no database. It reads the csv off disk every time you load a page.

Three people use it. Its numbers go into the transport committee's quarterly paper.

## How it was built

Dana built it over six weeks, mostly by describing what she wanted to an AI assistant and checking the result. Each piece of work is one afternoon's job, and the history says what each one was for.

## What is in it

- **Hour of day.** What time people walk past, weekdays and weekends apart.
- **By month and by year.** Totals over time, and a table for the front of the paper.
- **North against south.** Whether the two sides of the crossing are balanced.
- **Event days.** A list of days that are nothing like a normal day, such as Grand Final Friday, Melbourne Cup and Christmas. Dana started keeping it because those days kept being read as real changes in how many people walk to work.
- **A csv download**, so people stop asking for the numbers by email.

## How to run it

    python3 -m footfall.server

Then open http://localhost:8000. To check nothing is broken:

    python3 check.py

It renders every page and fails loudly if one of them throws.

## Its condition

It works. The smoke check passes. It has never had a second maintainer.

