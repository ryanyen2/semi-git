# Your tasks

You are the new maintainer of **confplan**, the program you just read about, a small command line tool that a conference committee uses to plan a two-day program. Sam Park built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

You have the code, its full history, and your assistant. There are three requests to work through in order and two short questions about what a past piece of work touched, 28 minutes of work in total. Every card has its own clock, and running out of time on one is a normal result.

Tell us what you are thinking as you go.

The two short questions are answered on screen, not on this sheet.

## Request 1: What changed talk search?

You have 5 minutes.

A committee support ticket says this:

> Talk search lists session times in a format I don't recognise, like
> `[Mon 09:00-10:30, Tue 13:00-14:30]`. Around the same time the app started
> accepting lowercase day names, like `mon 09:00-10:30`.

Go and find out what actually happened, then answer the three questions below.

> A ticket like this is really three questions. Someone reports that
> something looks different. You want to know **which piece of work** changed it,
> **when** that work landed, and **what else** the same piece of work touched on
> its way past, because the thing that broke is often not the thing the change
> was for.
>
> You do not have to answer in that order, and there is no expected route. Read
> the code, read the history, ask your assistant, or all three.

**Were the two things in the ticket one piece of work, or two?**

- One piece of work. Both arrived together.
- Two, days apart.
- Two, on the same day.
- I could not tell.

**When did it land?**

- The week of 29 June
- The week of 6 July
- The week of 20 July
- The week of 3 August
- I could not tell.

**Did anything else come along with it that the change was not advertised as doing?**

- No, just the search command and its tests.
- Yes, a change to how day names are read when a slot is parsed.
- Yes, a change to how capacity limits are enforced.
- Yes, a change to the export format.
- I could not tell.

Then say how sure you are, anywhere from guessing to certain.

## Requests 2 and 3: Take the waitlist out, then unregistering still needs to work

You have 15 minutes for requests 2 and 3 together.

### Request 2: Take the waitlist out

The committee has decided that waitlists are the registration desk's job now. For
the next release the waitlist has to be gone. That means attendees can no longer
join a waitlist, nobody is promoted off it when a seat frees up, and the seat
notices stop.

Everything else has to keep working exactly as it does today. That includes
registering, capacity limits, clash checks, talk search, exports, statistics,
and the room audit. Adjacent sessions are legal and must stay legal.

The test suite is your safety net. When you think you are done, `pytest -q`
should pass, except for the waitlist's own tests, which may be gone.

> Most of this request is finding the right thing, not removing it.
> The waitlist was not built in one go and other work landed on top of it, so the
> first job is working out how far it reaches.

### Request 3: Unregistering still needs to work

One correction to the last request. Attendees must still be able to unregister
from a session themselves. Bring the unregister command back, without any
waitlist promotion happening when a seat frees up.
