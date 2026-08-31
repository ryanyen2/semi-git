# Your stages: footfall, sgt

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

- `sgt now` says where things stand.
- `git diff` shows the change line by line, and `git status` lists the files it touches. Nothing is recorded yet, so this is where the detail is.
- In the editor, the Changes view and the diff view show the same edits.
- `sgt save -m "your words"` records the change and prints which piece of work it went under.

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

- `sgt log` lists the jobs somebody did, newest first, in their own words.
- `sgt find "the bit that works out the averages"` searches by description. Any wording will do.
- `sgt intent list` prints every feature and checkpoint with the handle you can type back, and the groups that span several features at the bottom.
- `sgt show "<name>"` shows what one piece of work covers.

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

- `sgt revert "<name>"` shows you what the removal would do and changes nothing.
- Add `--yes` to actually do it: `sgt revert "<name>" --yes`.
- The name is the one `./stage 3` printed. `sgt intent list` prints it too, at the bottom, with the groups that span several features.
- `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

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

- `sgt restore "<name>" --yes` puts back what `sgt revert` took out. It takes the same name.
- Without `--yes` you get a preview and nothing happens.
- `sgt log` and `sgt now` say what the history records so far.
- `sgt undo` reverses whatever you last did.
