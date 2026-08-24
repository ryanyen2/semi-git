# Your tasks

You have taken over **bikecount**, the dashboard you just read about, a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle. Dana Whitfield built it over the last six weeks, mostly by describing what she wanted to an AI assistant, and has now left the team.

You have the code, its full history, and your assistant. There are four cards to work through in order, 19 minutes of work in total. Some cards tell you exactly what to run. The ones that matter leave it entirely to you, and those are marked. Every card has its own clock, and running out of time on one is a normal result rather than a failure.

Tell us what you are thinking as you go.

Where a step asks you to tick things, the list is on screen and not on this sheet.

## Card 1: What does it leave out?

You have 3 minutes.

Open the dashboard and look at the hour of day page.

    python3 -m bikecount.server

The averages on that page do not count every day in the file. Some days are left out on purpose. Use the app, and the wording on the page itself, to work out which days those are and why somebody decided to leave them out.

Nothing here is scored and there is no expected wording.

**Which days are left out, and what reason is given?**

## Card 2: Who did that, and when?

You have 5 minutes.

Leaving those days out was a decision somebody made at some point in this project's history. Find the piece of work that made it.

**How you do that is entirely up to you.** There is no script and no suggested route. This is the part we are watching.

Put its name in the box: a commit hash, a feature name, a chapter name, an id, whatever your setup calls the thing you found. If you are not certain, write down what you have and say so. That is a real answer and it beats a guess.

**The piece of work that did it:** ______________________

## Card 3: Take it out

You have 6 minutes.

The committee has been clear that it wants the averages to count every day the sensors recorded, including the unusual ones. They never asked for days to be dropped.

Take that piece of work out. Every other part of the dashboard has to keep working.

Before you run anything that changes the project, tick which parts of the dashboard you think this will change. One minute, then it submits itself. You are not graded on it and you will not be shown an answer. You are about to find out for yourself.

Then do it, and run the smoke check to see where you ended up:

    python3 check.py

**Before you change anything:** The work you found in card 2: the one that keeps unusual days out of the averages.

On screen there is a list of the things you can see on bikecount's pages. Tick the ones you think this will affect. 60 seconds, then it submits itself. You answer once more afterwards, knowing what happened.

## Card 4: Put it back

You have 5 minutes.

The committee has changed its mind. Having seen the averages with every day counted, they now agree with Dana: a snowstorm that shut the city says nothing about how many people cycle to work, and it should come out of the averages after all.

Put the work you just removed back, exactly as it was, and check the dashboard matches what it showed at the start.

If you run out of clock, stop where you are. Not finishing is a normal outcome here and it is recorded as one.
