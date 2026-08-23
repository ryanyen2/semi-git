# Your tasks

You are the new maintainer of **coursecraft**, the program you just read about, a small command line tool that a university department uses to manage course registration. Riley Chen built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

You have the code, its full history, and your assistant. There are four cards to work through in order, 24 minutes of work in total. Some steps tell you exactly what to run; the ones that matter leave it entirely to you, and those are marked. Every card has its own clock, and running out of time on one is a normal result rather than a failure.

Tell us what you are thinking as you go.

Where a step asks you to tick things, the list is on screen and not on this sheet.

## Step 1: Two classes back to back

You have 3 minutes.

A support ticket came in this morning:

> A student is trying to take two sections of CS101 — one Monday 09:00–10:30,
> the other Monday 10:30–12:00. The system says they clash. They don't overlap.
> The room audit is doing the same thing to two bookings that run back to back
> in one room.

Run the script below. It works on a scratch copy of the data, so nothing you do
here touches the project.

Then say in your own words what is wrong and what the program should do instead.
There is no expected wording and this is not scored.

```
./show-the-problem.sh
```

It:

- makes a scratch store with one course and two sections, Mon 09:00–10:30 and Mon 10:30–12:00, in the same room
- enrols one student in the first section, then tries the second
- runs the room audit over the two bookings
- runs the project’s own tests for conflicts and rooms

**What is wrong, and what should the program do instead? A sentence or two is plenty.**

## Step 2: Where did that come from?

You have 5 minutes.

Something in this project's past made the program behave that way. Find out which
piece of work it was.

**How you do that is entirely up to you.** This is the part we are watching, so
there is no script and no suggested route.

When you have it, put its identifier in the box: a commit hash, a feature name,
an id — whatever your setup calls the thing you found. If you are not certain,
write down what you have and say you are not certain. That is a real answer and
it is better than a guess.

**The piece of work that caused it:** ______________________

## Step 3: Take it back out

You have 6 minutes.

Take that piece of work out, so back-to-back sections behave the way you said they
should. Everything else in the program has to keep working.

**Before you run anything that changes the project**, tick what you think it will
affect. One minute, then it submits itself.

You are not being graded on this and you will not be shown an answer. You are
about to find out for yourself.

Then do it, and run `./check.sh` to see where you ended up.

**Before you change anything:** The piece of work you found in step 2 — the one that changed how two time ranges are compared.

On screen there is a list of twelve things people do with coursecraft. Tick the ones you think this will affect. 60 seconds, then it submits itself. You answer once more afterwards, knowing what happened.

```
./check.sh
```

It:

- repeats step 1’s two back-to-back cases and prints what the program says now
- runs the whole test suite and prints which feature areas pass
- starts the command line tool, because a suite can pass in a program that will not start

## Step 4a to 4c: See what the waitlist does today, then take the waitlist out, then drops still need to work

You have 10 minutes for step 4a to 4c together.

### Step 4a: See what the waitlist does today

The next request is about the waitlist. Before it, see what the waitlist actually
does, so "gone" means something specific rather than something you have to guess
at.

The three parts of this card share one clock. Read all three before you start.

```
./show-the-waitlist.sh
```

It:

- fills a one-seat section and puts two students in the queue behind it
- shows the queue in order
- drops the enrolled student, so a seat frees up
- shows the freed seat being filled from the queue, and the notice that goes out

### Step 4b: Take the waitlist out

The department has decided that waitlists are the registrar's job now. For the
next release the waitlist has to be gone: joining a queue, the queue itself, the
automatic filling of a freed seat, and the notices that go with it.

Everything else in the program has to keep working.

If you run out of clock, stop where you are. **Not finishing is a normal
outcome here and it is recorded as one.** It is not a mark against you, and we
would rather see where you got to than have you rush the last step.

### Step 4c: Drops still need to work

One correction to the last request. Students must still be able to drop a section
themselves. Bring dropping back, with no automatic filling of the freed seat when
it happens.

When you are done, run `./check.sh` once more.
