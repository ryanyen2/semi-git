# Your tasks

You are the new maintainer of a program called confplan. It's a small command
line tool that a conference committee uses to plan a two-day program. Sam Park
built it over the last six weeks, partly by working with an AI assistant, and
has now left the team.

You have the code, its full history, and your assistant. Four requests have come
in. Work through them in order. Two more requests are at the end if you have
time.

Tell us what you are thinking as you go.

## Request 1: what changed talk search?

You have 7 minutes.

A committee support ticket says this:

> Talk search lists session times in a format I don't recognise, like
> `[Mon 09:00-10:30, Tue 13:00-14:30]`. Around the same time the app started
> accepting lowercase day names, like `mon 09:00-10:30`. Were those one change
> or two? What was the actual piece of work, when did it land, and did anything
> else come along with it?

Write two or three sentences answering the ticket. Put your answer in
`notes/answers.md`. Also say how confident you are.

## Request 2: take the waitlist out

You have 15 minutes for this request and the next one together.

The committee has decided that waitlists are the registration desk's job now.
For the next release the waitlist has to be gone. That means attendees can no
longer join a waitlist, nobody is promoted off it when a seat frees up, and the
seat notices stop.

Everything else has to keep working exactly as it does today. That includes
registering, capacity limits, clash checks, talk search, exports, statistics,
and the room audit. Adjacent sessions are legal and must stay legal.

The test suite is your safety net. When you think you are done, `pytest -q`
should pass, except for the waitlist's own tests, which may be gone.

## Request 3: unregistering still needs to work

Do this inside the 15 minutes above.

One correction to the last request. Attendees must still be able to unregister
from a session themselves. Bring the unregister command back, without any
waitlist promotion happening when a seat frees up.

## Request 4: back to back registration broke

You have 10 minutes.

Track chairs report that since late July attendees can no longer register for
back to back sessions. If an attendee is in a session that runs 09:00 to 10:30,
the app now refuses to register them for one that runs 10:30 to 12:00, and calls
it a clash. It used to work, and there was even a fix that specifically made
adjacent sessions legal.

Find what changed it and restore the old behavior. Keep whatever else that
change was doing. In particular the room audit has to keep working.

## Request 5: two ways to swap

You have 12 minutes. This one is optional.

Attendees want to swap between two sessions of the same talk in one step. There
are two reasonable ways to build it.

- Do the whole swap at once, and if the new session refuses the attendee, put
  them back in the old one.
- Unregister the attendee first and hold their seat for a moment, then register
  them.

Build both with your assistant, as two separate attempts. Then keep whichever
one you prefer and get rid of the other cleanly. Tell us why you kept the one
you kept.

## Request 6: clean up that tangled change

This one is optional and there is no time limit.

The change you looked at in request 1 bothers you. Two unrelated pieces of work
landed as one unit. Separate them in the history so each one has a clear name.
Don't change any of the current code.
