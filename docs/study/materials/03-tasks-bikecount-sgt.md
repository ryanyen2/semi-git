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

## Stage 1: Get to know the project

You have 5 minutes for this task.

Run the command below first. It puts the project into this stage's starting state.

    ./stage 1

**What happened:** You have just joined this project. Nothing is wrong with it, and there is nothing to fix in this stage.

**Your job:** Work out what this project is made of. Put the dashboard beside your setup's view of the history, and fill in the map below, right to left: for each part of the dashboard, which piece of work in the history put it there and where in the code that work lives. The first row is filled in as an example.

| The work | Where it lives in the code | What it puts on the dashboard |
|---|---|---|
| *(example)* the work that added the hour-of-day page | `pages/hourly.py` draws it, `metrics.py` works out the averages | **The busiest hour, and the hour-of-day chart under it** ![The hourly page: the busiest hour, and the average count by hour of day](/stages/bikecount-hourly.png) |
| | | **The month-by-month chart** ![The monthly page](/stages/bikecount-monthly.png) |
| | | **The one-row-per-year table** ![The by-year page](/stages/bikecount-yearly-plain.png) |
| | | **The east against west comparison** ![The two-sensor comparison page](/stages/bikecount-sides.png) |

File names are relative to the project's package, the one the practice sheet lists — `pages/hourly.py` is the hour-of-day page.

The date window at the top of every page is the one control on the dashboard. The pages open on the most recent year; set it to 2018 if you want to see the whole of a year.

**You are done when:** you could point at any part of the dashboard and say which piece of work in the history put it there, and roughly where in the code that work lives. Nothing in the map has to be written down — the questions after this stage are about one piece of work in particular.

What `./stage 1` does:

- resets the project to its full recorded history, with nothing of anyone else's left in it

Commands that may help:

- `sgt log` is the feature map: one row per part of the project, edits over time. `sgt log --rail` lists what happened, newest first.
- `sgt show "<name>"` says what one part covers — the files and the code inside it. `sgt show <file>::<name>` answers the other direction, for one function.
- `sgt find "the bit that works out the averages"` searches by description. Any wording will do.
- `sgt log --focus "<name>"` opens one row, or one ◆ piece of work that spans several rows.

## Stage 2: Find the work behind the wrong number

You have 4 minutes for this task.

Run the command below first. It resets the project and prints the two numbers this stage is about, side by side.

    ./stage 2

**What happened:** The cycling team published a report last year saying that the average day in 2018 saw **2,882** crossings. The dashboard's by-year page now says **2,900** for the same year. The numbers disagree because a colleague changed the way the dashboard works out an average. Days on the project's list of unusual days, such as the February 2019 snowstorm and Christmas, are now left out of every average, and the report was written when every day still counted.

![The by-year page, with the 2018 row marked — its average-day number is the one that disagrees with the report](/stages/bikecount-yearly.png)

The pages open on the most recent year. Set the date window at the top to cover 2018 — the year both numbers are about.

**Your job:** Find the piece of work in the project's history that made that change. You do not have to change any code.

**You are done when:** You can name the piece of work — a commit hash, a named piece of work, or an id all count. The questions after this stage ask you which one you found. If you are not certain, choose what you have and say that you are not certain. That is more useful to us than a guess.

What `./stage 2` does:

- puts the project back to its full history, discarding anything from the last stage
- prints the number the report quotes next to the number the dashboard shows

Commands that may help:

- `sgt log` shows the history grouped by feature; `sgt log --rail` lists what happened, newest first.
- `sgt find "the bit that works out the averages"` searches by description. Any wording will do.
- `sgt log --focus "<name>"` opens one feature — or one ◆ piece of cross-feature work: the map stays, its chapters are listed underneath.
- To answer "which parts of the dashboard": `sgt show "<name>"` lists the files a feature — or a ◆ piece of cross-feature work — touches, and what taking it out would remove. `pages/<name>.py` is the page of the same name, and the workbench shows the same card.

## Stage 3: Take that work out

You have 4 minutes for this task.

Run the command below first. It resets the project and names the work you have to take out, so you have the name whether or not you found it in the last stage.

    ./stage 3

**What happened:** The committee never approved the change your colleague made. They want the averages to count every day the sensors recorded, including the unusual ones.

**Your job:** Take that work out of the project. Three things have to go: the list of unusual days the project keeps, the marks that flag those days on the daily and monthly charts, and the rule that leaves those days out of the averages. Everything else the dashboard shows has to keep working.

![The monthly page today: the coloured bars flag months containing an unusual day. After the removal, no bar is coloured and the note under the chart is gone.](/stages/bikecount-monthly.png)

Set the date window to 2018 while you check the pages — the marks only show when the window contains an unusual day, and the pages open on the most recent year.

**You are done when:** `./check 3` says the program still runs and the by-year page reads **2,882** for 2018 again. Run it as often as you like. It prints the same words for everyone, it does not mark you, and a red line in it is information rather than a verdict.

What `./stage 3` does:

- puts the project back to its full history, discarding anything from the last stage
- names the work to take out, in the words this setup uses for it

Commands that may help:

- `sgt revert "<name>"` shows you what the removal would do and changes nothing.
- Add `--yes` to actually do it: `sgt revert "<name>" --yes`.
- The name is the one `./stage 3` printed. `sgt log` names it under the map too, with the other work that spans features, and `sgt log --focus "<name>"` shows exactly what is in it.
- `sgt undo` reverses whatever you last did, and `sgt now` says where things stand.

## Stage 4: Put it back

You have 4 minutes for this task.

Run the command below first. It puts the project into the state where the work has already been taken out. Everyone starts stage 4 from this same state, whether or not their own removal in the last stage worked.

    ./stage 4

**What happened:** The committee has changed its mind. Now that they have seen the averages with every day counted, they agree with your colleague that a snowstorm that shut the city says nothing about how many people cycle to work on an ordinary day, so those days should stay out of the averages after all.

**Your job:** Put that work back into the project, exactly as it was before the removal.

![The by-year page you are aiming for: the marked 2018 row reads its excluded-days number again](/stages/bikecount-yearly.png)

**You are done when:** `./check 4` says the program still runs and the by-year page reads **2,900** for 2018 again.

What `./stage 4` does:

- puts the project in the state where that work has already been taken out, the same for everyone

Commands that may help:

- `sgt restore "<name>" --yes` puts back what `sgt revert` took out. It takes the same name.
- Without `--yes` you get a preview and nothing happens.
- `sgt log` and `sgt now` say what the history records so far.
- `sgt undo` reverses whatever you last did.
