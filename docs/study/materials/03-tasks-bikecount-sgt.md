# Your stages: bikecount, sgt

You are now responsible for **bikecount**, a small web dashboard over the bicycle counter on the Fremont Bridge in Seattle. Dana Whitfield built the project over six weeks before leaving the team. Its numbers are used in a quarterly report.

You just practiced on this project. Keep using the same folder and the same terminal.

You will complete four stages in order.

Each stage tells you:

- what happened
- what you need to do
- how to tell when you are finished
- which commands may help

Start each stage by running the `./stage` command shown on the card. This prepares the correct starting state. The timer begins after that command finishes.

The task itself is timed. The questions after the task are untimed. When the timer ends, continue to the questions and then move to the next stage.

Please say what you are thinking as you work.

After each stage, answer the short questions on the screen. These questions are untimed.

## Stage 1: Record what the assistant did

You have 4 minutes for this task.

Run the command below first. It puts the project into this stage's starting state.

    ./stage 1

**What happened:** Earlier today you asked the coding assistant to round the numbers on the dashboard's front page to the nearest ten, so that they stop implying one-bike precision. The assistant has finished. Its changes are in your working copy, and none of them are recorded in the project's history yet.

**Your job:** Read what the assistant changed, in the editor or in the terminal, until you could describe it to a colleague. Then record all of it, the way this setup records finished work.

**You are done when:** Every one of the assistant's changes is recorded in the project's history, with a message in your own words, and nothing is left unrecorded.

When you run `./stage 1`, it will:

- resets the project to this stage's starting state
- replays the assistant's changes into your working copy, unrecorded

Commands that may help:

- `sgt now` says where things stand.
- `git diff` shows the change line by line, and `git status` lists the files it touches. Nothing is recorded yet, so this is where the detail is.
- In the editor, the Changes view and the diff view show the same edits.
- `sgt save -m "your words"` records the change and prints which piece of work it went under.

## Stage 2: Find the work behind the wrong number

You have 4 minutes for this task.

Run the command below first. It resets the project and prints the two numbers this stage is about, side by side.

    ./stage 2

**What happened:** The cycling team published a report last year saying that the average day in 2018 saw **2,882** crossings. The dashboard's by-year page now says **2,900** for the same year. The numbers disagree because a colleague changed the way the dashboard works out an average. Days on the project's list of unusual days, such as the February 2019 snowstorm and Christmas, are now left out of every average, and the report was written when every day still counted.

**Your job:** Find the piece of work in the project's history that made that change. You do not have to change any code.

**You are done when:** You can name the piece of work — a commit hash, a named piece of work, or an id all count. The questions after this stage ask you which one you found. If you are not certain, choose what you have and say that you are not certain. That is more useful to us than a guess.

When you run `./stage 2`, it will:

- puts the project back to its full history, discarding anything from the last stage
- prints the number the report quotes next to the number the dashboard shows

Commands that may help:

- `sgt log` shows the history grouped by feature; `sgt log --rail` lists what happened, newest first.
- `sgt find "the bit that works out the averages"` searches by description. Any wording will do.
- `sgt intent list` prints every feature and checkpoint with the handle you can type back, and the groups that span several features at the bottom.
- `sgt show "<name>"` shows what one piece of work covers.

## Stage 3: Take that work out

You have 4 minutes for this task.

Run the command below first. It resets the project and names the work you have to take out, so you have the name whether or not you found it in the last stage.

    ./stage 3

**What happened:** The committee never approved the change your colleague made. They want the averages to count every day the sensors recorded, including the unusual ones.

**Your job:** Take that work out of the project. Three things have to go: the list of unusual days the project keeps, the marks that flag those days on the daily and monthly charts, and the rule that leaves those days out of the averages. Everything else the dashboard shows has to keep working.

**You are done when:** `./check 3` says the program still runs and the by-year page reads **2,882** for 2018 again. Run it as often as you like. It prints the same words for everyone, it does not mark you, and a red line in it is information rather than a verdict.

When you run `./stage 3`, it will:

- puts the project back to its full history, discarding anything from the last stage
- names the work to take out, in the words this setup uses for it

Commands that may help:

- `sgt revert "<name>"` shows you what the removal would do and changes nothing.
- Add `--yes` to actually do it: `sgt revert "<name>" --yes`.
- The name is the one `./stage 3` printed. `sgt intent list` prints it too, at the bottom, with the groups that span several features.
- `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

## Stage 4: Put it back

You have 4 minutes for this task.

Run the command below first. It puts the project into the state where the work has already been taken out. Everyone starts stage 4 from this same state, whether or not their own removal in the last stage worked.

    ./stage 4

**What happened:** The committee has changed its mind. Now that they have seen the averages with every day counted, they agree with your colleague that a snowstorm that shut the city says nothing about how many people cycle to work on an ordinary day, so those days should stay out of the averages after all.

**Your job:** Put that work back into the project, exactly as it was before the removal.

**You are done when:** `./check 4` says the program still runs and the by-year page reads **2,900** for 2018 again.

When you run `./stage 4`, it will:

- puts the project in the state where that work has already been taken out, the same for everyone

Commands that may help:

- `sgt restore "<name>" --yes` puts back what `sgt revert` took out. It takes the same name.
- Without `--yes` you get a preview and nothing happens.
- `sgt log` and `sgt now` say what the history records so far.
- `sgt undo` reverses whatever you last did.
