# Your stages: footfall, git

You are looking after **footfall**, a small web dashboard over the pedestrian counter on Spencer Street in Melbourne. Dana Whitfield built it over six weeks and has left the team. It reads a public csv of hourly sensor counts and renders a handful of pages: a front page, an hour-of-day page, monthly and yearly totals, a comparison of the two sensors, and a csv download. Its numbers go into a quarterly report.

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

Earlier today you asked the coding assistant to round the numbers on the dashboard's front page to the nearest ten, so that they stop implying single-person precision. The assistant has finished. Its changes are sitting in your working copy, and none of them have been recorded in the project's history yet.

Read what it changed, in the editor or in the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.

What `./stage 1` does:

- resets the project to this stage's starting state
- replays the assistant's changes into your working copy, unrecorded

Commands you may want:

- `git status` lists the files that have changed but are not recorded yet.
- `git diff` shows what changed inside them.
- `git add <file>` then `git commit -m "your words"` records the change. `git add -A` stages everything at once.
- In the editor, the Source Control panel shows the same files and commits them.

## Stage 2: Find the work behind the wrong number

You have 4 minutes for the doing part.

This stage's task is to find one piece of work in the project's history. You do not have to change anything.

Start by running the command below. It puts the project back to its full history and clears anything left over from the last stage.

    ./stage 2

The transport committee published a paper last year. It says that the average day in 2018 saw **42,436** people walk past. The dashboard's by-year page now says **42,545** for the same year. The command above prints both numbers so that you can see them side by side.

Here is what happened. A colleague changed the way the dashboard works out an average. Days on the project's list of unusual days, such as Grand Final Friday and Christmas, are now left out of every average. The colleague had a reason for doing that, but the paper was written when every day still counted, and the committee wants the two numbers to agree again.

Find the work in the project's history that made that change, and write down what this setup calls it in the box below. That might be a commit hash, a named piece of work, or an id. If you are not certain, write down what you have and say that you are not certain. That is more useful to us than a guess.

What `./stage 2` does:

- puts the project back to its full history, discarding anything from the last stage
- prints the number the paper quotes next to the number the dashboard shows

Commands you may want:

- `git log --oneline` lists the commits, newest first.
- `git show <hash>` shows what one commit changed.
- `git log --oneline -S "average"` finds the commits where a piece of text arrived or went away. Any word from the code works.
- `git log --oneline -- <file>` narrows that to one file, and `git blame <file>` says which commit last touched each line.

**What this setup calls the work that changed the averages:** ______________________

## Stage 3: Take that work out

You have 4 minutes for the doing part.

This stage's task is to take one piece of work back out of the project.

Start by running the command below. It puts the project back to its full history, clears anything left over from the last stage, and names the work you have to take out. You have that name whether or not you found it yourself in the last stage.

    ./stage 3

The committee never approved the change your colleague made. They want the averages to count every day the sensors recorded, including the unusual ones, so that the by-year page reads **42,436** for 2018 again.

Three things have to come out: the list of unusual days the project keeps, the marks that flag those days on the daily and monthly charts, and the rule that leaves those days out of the averages. They were one job, and the project's history has them spread over three commits. Everything else the dashboard shows has to keep working.

When you think you are done, run:

    ./check 3

It tells you whether the program still runs and what the by-year page says now. It prints the same words for everyone, it does not mark you, and a red line in it is information rather than a verdict.

What `./stage 3` does:

- puts the project back to its full history, discarding anything from the last stage
- names the work to take out, in the words this setup uses for it

Commands you may want:

- `git revert <hash>` makes a new commit that undoes an old one. Give it the oldest of the three last.
- If it stops on a conflict, fix the marked lines, `git add` the file, then `git revert --continue`.
- `git revert --abort` walks away from a revert that has gone wrong and leaves nothing behind.
- `git log --oneline` and `git status` say where you are at any point.

## Stage 4: Put it back

You have 4 minutes for the doing part.

This stage's task is to put that same work back into the project.

Start by running the command below. It puts the project into the state where the work has already been taken out. That is the same starting state for everyone, whether or not your own removal in the last stage worked, so nothing from the last stage follows you here.

    ./stage 4

The committee has changed its mind. Now that they have seen the averages with every day counted, they agree with your colleague: a public holiday when the offices are shut says nothing about how many people walk to work on an ordinary day, so those days should stay out of the averages after all.

Put the work back, exactly as it was, so that the by-year page reads **42,545** for 2018 again.

When you think you are done, run:

    ./check 4

What `./stage 4` does:

- puts the project in the state where that work has already been taken out, the same for everyone

Commands you may want:

- The removal is three commits at the top of the history. `git log --oneline` shows them.
- `git revert <hash>` on a revert commit undoes the undoing.
- If it stops on a conflict, fix the marked lines, `git add` the file, then `git revert --continue`.
- `git show <hash>` reads any one of them if you want to see what it did.
