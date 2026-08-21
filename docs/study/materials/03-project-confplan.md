# The project: confplan

Nothing is timed on this page. Read it at whatever pace you want and ask anything you like before you continue. You do not have to memorise any of it. The requests tell you what needs to change, and this is here so that finding your way around is not the first thing you have to do under a clock.

You are taking over **confplan**. Sam Park built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

## What it is for

A conference committee plans its two-day program by hand every year: a spreadsheet of who is booked into which talk, a second one for room bookings, and a lot of email. confplan replaces that. It is a command line tool. One person on the committee runs it, and everything it knows lives in a single file on disk.

## Who uses it, and how

Someone on the committee types commands at a terminal. There is no web page and no login. A normal year looks like this:

- While the program is being built they add the **talks** that were accepted, and for each talk the **sessions** it is given. A session is one scheduled slot, with a speaker, a room, a time on one of the two days, and a cap on how many people fit.
- Attendees get added to the system, then **registered** into sessions.
- When an attendee wants out, they are **unregistered** from a session, which frees the seat.

## What it refuses to do

Most of the value is in what it stops you doing by accident. When someone is registered, the app checks that:

- the session is not already full,
- the attendee has been to whatever the talk expects first,
- and the new session does not clash with something else in their day.

A session that ends at 10:30 and one that starts at 10:30 do not clash. That was a deliberate decision.

## The rest of it

- **Waitlists.** When a session is full an attendee can join a queue instead. When a seat frees up the next person in the queue gets it, and a notice is left for the committee to pass on.
- **Search.** Find talks by typing part of a title or a topic.
- **Views and exports.** One attendee's day, one speaker's schedule, the whole program as a spreadsheet, and a count of how full everything is.
- **A room audit**, which lists rooms that got double booked.

## Its condition

It works and it has a test suite that passes. It has never had a second maintainer.
