# The project: coursecraft

You are taking over **coursecraft**. Riley Chen built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

Nothing is timed on this page. Read it at whatever pace you want and ask anything you like before you continue. You do not have to memorise any of it — the requests tell you what needs to change. This is here so that finding your way around is not the first thing you have to do under a clock.

## What it is for

A university department runs course registration by hand every term: a spreadsheet of who is in which class, a second one for room bookings, and a lot of email. coursecraft replaces that. It is a command line tool. One person in the department office runs it, and everything it knows lives in a single file on disk.

## Who uses it, and how

Someone in the office types commands at a terminal. There is no web page and no login. A normal term looks like this:

- At the start of term they add the **courses** the department is running, and for each course the **sections** it is taught in — a section is one timetabled group, with a teacher, a room, a weekly time slot, and a cap on how many people fit.
- Students get added to the system, then **enrolled** into sections.
- When a student wants out, they are **dropped** from a section, which frees the seat.

## What it refuses to do

Most of the value is in what it stops you doing by accident. When someone is enrolled, the app checks that:

- the section is not already full,
- the student has passed whatever the course requires first,
- and the new section does not clash with something else in their week.

A section that ends at 10:30 and one that starts at 10:30 do not clash. That was a deliberate decision.

## The rest of it

- **Waitlists.** When a section is full a student can join a queue instead. When a seat frees up the next person in the queue gets it, and a notice is left for the office to pass on.
- **Search.** Find courses by typing part of a name or a topic.
- **Views and exports.** One student's week, one teacher's timetable, the whole catalogue as a spreadsheet, and a count of how full everything is.
- **A room audit**, which lists rooms that got double booked.

## Its condition

It works and it has a test suite that passes. It has never had a second maintainer.
