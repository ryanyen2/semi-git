# Your tasks

You are the new maintainer of **confplan**, the program you just read about, a small command line tool that a conference committee uses to plan a two-day program. Sam Park built it over the last six weeks, partly by working with an AI assistant, and has now left the team.

You have the code, its full history, and your assistant. There are four cards to work through in order, 24 minutes of work in total. Some steps tell you exactly what to run; the ones that matter leave it entirely to you, and those are marked. Every card has its own clock, and running out of time on one is a normal result rather than a failure.

Tell us what you are thinking as you go.

Where a step asks you to tick things, the list is on screen and not on this sheet.

## Step 1: Two sessions back to back

You have 3 minutes.

A support ticket came in this morning:

> An attendee is trying to register for two sessions of T1 — one Saturday
> 09:00–10:30, the other Saturday 10:30–12:00. The system says they clash. They
> don't overlap. The room audit is doing the same thing to two bookings that run
> back to back in one room.

Run the script below. It works on a scratch copy of the data, so nothing you do
here touches the project.

Then say in your own words what is wrong and what the program should do instead.
There is no expected wording and this is not scored.

```
./show-the-problem.sh
```

It:

- makes a scratch store with one talk and two sessions, Sat 09:00–10:30 and Sat 10:30–12:00, in the same room
- registers one attendee for the first session, then tries the second
- runs the room audit over the two bookings
- runs the project’s own tests for clashes and rooms

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

Take that piece of work out, so back-to-back sessions behave the way you said they
should. Everything else in the program has to keep working.

**Before you run anything that changes the project**, tick what you think it will
affect. One minute, then it submits itself.

You are not being graded on this and you will not be shown an answer. You are
about to find out for yourself.

Then do it, and run `./check.sh` to see where you ended up.

**Before you change anything:** The piece of work you found in step 2 — the one that changed how two time ranges are compared.

On screen there is a list of twelve things people do with confplan. Tick the ones you think this will affect. 60 seconds, then it submits itself. You answer once more afterwards, knowing what happened.

```
./check.sh
```

It:

- repeats step 1’s two back-to-back cases and prints what the program says now
- runs the whole test suite and prints which feature areas pass
- starts the command line tool, because a suite can pass in a program that will not start

## Step 4a to 4c: See what the queue does today, then take the queue out, then cancelling still needs to work

You have 10 minutes for step 4a to 4c together.

### Step 4a: See what the queue does today

The next request is about the queue for full sessions. Before it, see what the
queue actually does, so "gone" means something specific rather than something you
have to guess at.

The three parts of this card share one clock. Read all three before you start.

```
./show-the-waitlist.sh
```

It:

- fills a one-seat session and puts two attendees in the queue behind it
- shows the queue in order
- cancels the registered attendee, so a seat frees up
- shows the freed seat being filled from the queue, and the notice that goes out

### Step 4b: Take the queue out

The committee has decided that queues are the registration desk's job now. For
the next release the queue has to be gone: joining a queue, the queue itself, the
automatic filling of a freed seat, and the notices that go with it.

Everything else in the program has to keep working.

If you run out of clock, stop where you are. **Not finishing is a normal
outcome here and it is recorded as one.** It is not a mark against you, and we
would rather see where you got to than have you rush the last step.

### Step 4c: Cancelling still needs to work

One correction to the last request. Attendees must still be able to cancel a
registration themselves. Bring cancelling back, with no automatic filling of the
freed seat when it happens.

When you are done, run `./check.sh` once more.
