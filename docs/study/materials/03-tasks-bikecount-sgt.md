# Your stages: bikecount, sgt

You are looking after **bikecount**, a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle. Dana Whitfield built it over six weeks and has left the team. It reads a public csv of hourly sensor counts and renders a handful of pages: a front page, an hour-of-day page, monthly and yearly totals, a comparison of the two sensors, and a csv download. Its numbers go into a quarterly report.

This is the same project you have just been practising on, and you run these from the same folder.

There are four stages, in order. Each one starts with a command you run yourself, `./stage 1` and so on, which puts the project into that stage's starting state. The clock starts once that command has finished printing. The doing part has a visible countdown. The questions after it are not timed.

Every stage card carries a short list of the commands you are most likely to want. They are there so that you do not lose a minute to remembering a flag.

Running out of time on a stage is a normal result, and the next stage starts clean either way. Tell us what you are thinking as you go.

After each stage, the screen asks a few short questions about what you just did. They are not timed.

## Stage 1: Record what the assistant did

You have 4 minutes for the doing part.

This stage's task is to read a change an AI assistant has already made, and then record it in the project's history.

Start by running the command below. It puts the project into the state this stage begins from.

    ./stage 1

Earlier today you asked the coding assistant to round the numbers on the dashboard's front page to the nearest ten, so that they stop implying one-bike precision. The assistant has finished. Its changes are sitting in your working copy, and none of them have been recorded in the project's history yet.

Read what it changed, in the editor or in the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.

What `./stage 1` does:

- resets the project to this stage's starting state
- replays the assistant's changes into your working copy, unrecorded

Commands you may want:

- `sgt now` says where things stand.
- `git diff` shows the change line by line, and `git status` lists the files it touches. Nothing is recorded yet, so this is where the detail is.
- In the editor, the Changes view and the diff view show the same edits.
- `sgt save -m "your words"` records the change and prints which piece of work it went under.

## Stage 2: Find the work behind the wrong number

You have 4 minutes for the doing part.

This stage's task is to find one piece of work in the project's history. You do not have to change anything.

Start by running the command below. It puts the project back to its full history and clears anything left over from the last stage.

    ./stage 2

The cycling team published a report last year. It says that the average day in 2018 saw **2,882** crossings. The dashboard's by-year page now says **2,900** for the same year. The command above prints both numbers so that you can see them side by side.

Here is what happened. A colleague changed the way the dashboard works out an average. Days on the project's list of unusual days, such as the February 2019 snowstorm and Christmas, are now left out of every average. The colleague had a reason for doing that, but the report was written when every day still counted, and the committee wants the two numbers to agree again.

Find the work in the project's history that made that change, and write down what this setup calls it in the box below. That might be a commit hash, a named piece of work, or an id. If you are not certain, write down what you have and say that you are not certain. That is more useful to us than a guess.

What `./stage 2` does:

- puts the project back to its full history, discarding anything from the last stage
- prints the number the report quotes next to the number the dashboard shows

Commands you may want:

- `sgt log` lists the jobs somebody did, newest first, in their own words.
- `sgt find "the bit that works out the averages"` searches by description. Any wording will do.
- `sgt intent list` prints every feature and checkpoint with the handle you can type back, and the groups that span several features at the bottom.
- `sgt show "<name>"` shows what one piece of work covers.

**What this setup calls the work that changed the averages:** ______________________

## Stage 3: Take that work out

You have 4 minutes for the doing part.

This stage's task is to take one piece of work back out of the project.

Start by running the command below. It puts the project back to its full history, clears anything left over from the last stage, and names the work you have to take out. You have that name whether or not you found it yourself in the last stage.

    ./stage 3

The committee never approved the change your colleague made. They want the averages to count every day the sensors recorded, including the unusual ones, so that the by-year page reads **2,882** for 2018 again.

Three things have to come out: the list of unusual days the project keeps, the marks that flag those days on the daily and monthly charts, and the rule that leaves those days out of the averages. They were one job, and the project's history has them spread over three commits. Everything else the dashboard shows has to keep working.

When you think you are done, run:

    ./check 3

It tells you whether the program still runs and what the by-year page says now. It prints the same words for everyone, it does not mark you, and a red line in it is information rather than a verdict.

What `./stage 3` does:

- puts the project back to its full history, discarding anything from the last stage
- names the work to take out, in the words this setup uses for it

Commands you may want:

- `sgt revert "<name>"` shows you what the removal would do and changes nothing.
- Add `--yes` to actually do it: `sgt revert "<name>" --yes`.
- The name is the one `./stage 3` printed. `sgt intent list` prints it too, at the bottom, with the groups that span several features.
- `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

## Stage 4: Put it back

You have 4 minutes for the doing part.

This stage's task is to put that same work back into the project.

Start by running the command below. It puts the project into the state where the work has already been taken out. That is the same starting state for everyone, whether or not your own removal in the last stage worked, so nothing from the last stage follows you here.

    ./stage 4

The committee has changed its mind. Now that they have seen the averages with every day counted, they agree with your colleague: a snowstorm that shut the city says nothing about how many people cycle to work on an ordinary day, so those days should stay out of the averages after all.

Put the work back, exactly as it was, so that the by-year page reads **2,900** for 2018 again.

When you think you are done, run:

    ./check 4

What `./stage 4` does:

- puts the project in the state where that work has already been taken out, the same for everyone

Commands you may want:

- `sgt restore "<name>" --yes` puts back what `sgt revert` took out. It takes the same name.
- Without `--yes` you get a preview and nothing happens.
- `sgt log` and `sgt now` say what the history records so far.
- `sgt undo` reverses whatever you last did.
