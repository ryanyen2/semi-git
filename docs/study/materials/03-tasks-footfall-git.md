# Your stages: footfall, git

You are looking after **footfall**, a small web dashboard over the pedestrian counter on Spencer Street in Melbourne. Dana Whitfield built it over six weeks and has left the team, and its numbers go into a quarterly report. It is the same project you have just been practising on, and you run everything from the same folder.

There are four stages, in order. Each stage card says what happened, what your job is, and what done looks like, and it lists the commands you are most likely to want, so you do not lose a minute to remembering a flag.

Start each stage by running its `./stage` command, which puts the project into that stage's starting state. The clock starts once that command has finished printing, the doing part has a visible countdown, and the questions after it are untimed. Running out of time on a stage is a normal result, and the next stage starts clean either way. Tell us what you are thinking as you go.

After each stage, the screen asks a few short questions about what you just did. They are not timed.

## Stage 1: Record what the assistant did

You have 4 minutes for the doing part.

Run the command below first. It puts the project into this stage's starting state.

    ./stage 1

**What happened:** Earlier today you asked the coding assistant to round the numbers on the dashboard's front page to the nearest ten, so that they stop implying single-person precision. The assistant has finished. Its changes are in your working copy, and none of them are recorded in the project's history yet.

**Your job:** Read what the assistant changed, in the editor or in the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.

**You are done when:** Every one of the assistant's changes is recorded in the project's history, with a message in your own words, and nothing is left unrecorded.

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

Run the command below first. It resets the project and prints the two numbers this stage is about, side by side.

    ./stage 2

**What happened:** The transport committee published a paper last year saying that the average day in 2018 saw **42,436** people walk past. The dashboard's by-year page now says **42,545** for the same year. The numbers disagree because a colleague changed the way the dashboard works out an average. Days on the project's list of unusual days, such as Grand Final Friday and Christmas, are now left out of every average, and the paper was written when every day still counted.

**Your job:** Find the piece of work in the project's history that made that change. You do not have to change any code.

**You are done when:** You have written what this setup calls that work into the box below. A commit hash, a named piece of work, or an id all count. If you are not certain you found the right one, write down what you have and say that you are not certain. That is more useful to us than a guess.

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

Run the command below first. It resets the project and names the work you have to take out, so you have the name whether or not you found it in the last stage.

    ./stage 3

**What happened:** The committee never approved the change your colleague made. They want the averages to count every day the sensors recorded, including the unusual ones.

**Your job:** Take that work out of the project. Three things have to go: the list of unusual days the project keeps, the marks that flag those days on the daily and monthly charts, and the rule that leaves those days out of the averages. Everything else the dashboard shows has to keep working.

**You are done when:** `./check 3` says the program still runs and the by-year page reads **42,436** for 2018 again. Run it as often as you like. It prints the same words for everyone, it does not mark you, and a red line in it is information rather than a verdict.

What `./stage 3` does:

- puts the project back to its full history, discarding anything from the last stage
- names the work to take out, in the words this setup uses for it

Commands you may want:

- `git revert <hash>` makes a new commit that undoes an old one. Give it the oldest of the three last.
- If it stops on a conflict, `git status` lists the unresolved files. Fix the marked lines and `git add` the file, or `git rm` a file the revert means to delete, then `git revert --continue`.
- `git revert --abort` walks away from a revert that has gone wrong and leaves nothing behind.
- `git log --oneline` and `git status` say where you are at any point.

## Stage 4: Put it back

You have 4 minutes for the doing part.

Run the command below first. It puts the project into the state where the work has already been taken out. Everyone starts stage 4 from this same state, whether or not their own removal in the last stage worked.

    ./stage 4

**What happened:** The committee has changed its mind. Now that they have seen the averages with every day counted, they agree with your colleague that a public holiday when the offices are shut says nothing about how many people walk to work on an ordinary day, so those days should stay out of the averages after all.

**Your job:** Put that work back into the project, exactly as it was before the removal.

**You are done when:** `./check 4` says the program still runs and the by-year page reads **42,545** for 2018 again.

What `./stage 4` does:

- puts the project in the state where that work has already been taken out, the same for everyone

Commands you may want:

- The removal is three commits at the top of the history. `git log --oneline` shows them.
- `git revert <hash>` on a revert commit undoes the undoing.
- If it stops on a conflict, `git status` lists the unresolved files. Fix the marked lines and `git add` the file, or `git rm` a file the revert means to delete, then `git revert --continue`.
- `git show <hash>` reads any one of them if you want to see what it did.
