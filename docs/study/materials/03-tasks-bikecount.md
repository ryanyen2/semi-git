# Your stages

You are looking after **bikecount**, a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle. Dana Whitfield built it over six weeks and has left the team. It reads a public csv of hourly sensor counts and renders a handful of pages: a front page, an hour-of-day page, monthly and yearly totals, a comparison of the two sensors, and a csv download. Its numbers go into a quarterly report.

You need to be in the project folder for these. If you have been practising, run `study-work` first.

There are four stages, in order. Each one starts with a command you run yourself, `./stage 1` and so on, which puts the project in that stage's starting state. The clock starts once that command has finished printing. The doing part has a visible countdown; the questions after it do not.

Running out of time on a stage is a normal result, and the next stage starts clean either way. Tell us what you are thinking as you go.

After each stage, the screen asks a few short questions about what you just did. They are not timed.

## Stage 1: Record what the assistant did

You have 4 minutes for the doing part.

Start by putting the project in this stage's starting state:

    ./stage 1

Earlier today you asked the coding assistant to round the numbers on the dashboard's front page to the nearest ten, so they stop implying one-bike precision. The assistant has finished. Its changes are in your working copy, and nothing is recorded in the project's history yet.

Read what it changed, in the editor or the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.

What `./stage 1` does:

- resets the project to this stage's starting state
- replays the assistant's changes into your working copy, unrecorded

## Stage 2: Find the work behind the wrong number

You have 4 minutes for the doing part.

Reset first. Anything left over from the last stage is gone after this, which is deliberate:

    ./stage 2

The cycling team published a report last year. It says the average day in 2018 saw **2,882** crossings. The dashboard's by-year page now says **2,900** for the same year. The reset script prints both numbers so you can see them side by side.

Here is what happened. A colleague changed how the dashboard works out an average. Days on the project's list of unusual days, like the February 2019 snowstorm and Christmas, are now left out of every average. There was a reason for it, but the report was written when every day still counted, and the committee wants the two to agree again.

Your job in this stage is only to find that work in the project's history. Put its name in the box: a commit hash, a named piece of work, an id, whatever this setup calls the thing you found. If you are not certain, write down what you have and say so. That beats a guess.

What `./stage 2` does:

- puts the project back to its full history, discarding anything from the last stage
- prints the number the report quotes next to the number the dashboard shows

**The piece of work that changed the averages:** ______________________

## Stage 3: Take that work out

You have 4 minutes for the doing part.

Reset first:

    ./stage 3

It names the work to take out, so you have it even if the last stage ran out of time. Finding it was that stage's job. This one is about the removal.

The committee never approved the change. They want the averages to count every day the sensors recorded, including the unusual ones, so the dashboard reads **2,882** for 2018 again. Take that piece of work out. Everything else the dashboard shows has to keep working.

When you think you are done:

    ./check 3

It prints the same words for everyone and tells you what the dashboard shows now. It does not mark you, and a red line in it is information rather than a verdict.

What `./stage 3` does:

- puts the project back to its full history, discarding anything from the last stage
- names the work to take out, in the words this setup uses for it

## Stage 4: Put it back

You have 4 minutes for the doing part.

Reset first:

    ./stage 4

This puts the project in the state where that work has already been taken out. It is the same state for everyone, whether or not your own removal worked, so nothing from the last stage follows you here.

The committee has changed its mind. Having seen the averages with every day counted, they now agree with your colleague: a snowstorm that shut the city says nothing about how many people cycle to work on an ordinary day, and it should stay out of the averages after all. Put the work back, exactly as it was, so 2018 reads **2,900** again.

When you think you are done:

    ./check 4

What `./stage 4` does:

- puts the project in the state where that work has already been taken out, the same for everyone
