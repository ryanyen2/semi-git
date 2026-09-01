# Tasks: footfall, sgt

You are taking over **footfall**, a small web dashboard over the pedestrian counter on Spencer Street in Melbourne, from Dana Whitfield. Its numbers are used in a quarterly report.

Complete the four tasks in order. Start each one with the `./stage` command shown on its card. The timer starts after the command finishes.

When you finish or the timer ends, answer the questions and continue to the next task.

## Stage 1: Get to know the project

You have 5 minutes for this task.

Start by running `./stage 1`.

Run the command below first. It puts the project into this stage's starting state.

    ./stage 1

**What happened:** You have just joined this project. Nothing is wrong with it, and there is nothing to fix in this stage.

**How it got here:** Dana Whitfield built the first version over the counter's own data file, and then asked for one change at a time until the dashboard was what you see now. An assistant did all of that later work, which is why every piece of it sits under a single name in the history.

**Your job:** Read the map below. It is the whole project, oldest work first. Then put it beside your setup's view of the history and work out what your setup calls each row.

| The work | Where it lives in the code | What it puts on the dashboard |
|---|---|---|
| the first version of the dashboard | `data.py` reads the counter's file, `metrics.py` adds it up, `charts.py` draws, `pages/overview.py` is the front page | **The front page: the busiest day, and the last fortnight chart** ![](/stages/footfall-overview.png) |
| the hour-of-day page | `pages/hourly.py` draws it, `metrics.py` works out the averages | **The busiest hour, and the hour-of-day chart under it** ![](/stages/footfall-hourly.png) |
| splitting that page into weekdays and weekends | `pages/hourly.py`, `charts.py` | **The weekday chart and the weekend chart, side by side** ![](/stages/footfall-hourly-split.png) |
| the list of unusual days the project keeps | `events.py` | Nothing on its own. It is the list the next two rows read. |
| the month-by-month page | `pages/monthly.py`, `metrics.py` | **The month-by-month chart** ![](/stages/footfall-monthly.png) |
| marking the unusual days on the charts | `charts.py`, `events.py`, `pages/monthly.py`, `pages/overview.py` | **The coloured bars on the daily and the month-by-month charts, and the note under each** |
| the north v south page | `pages/sides.py`, `metrics.py` | **The north against south comparison** ![](/stages/footfall-sides.png) |
| the by-year table | `pages/yearly.py`, `metrics.py` | **The one-row-per-year table** ![](/stages/footfall-yearly-plain.png) |
| leaving the unusual days out of the averages | `metrics.py` | No page of its own. It moves every average the dashboard shows, the by-year table's included. |
| the csv download | `server.py`, `pages/__init__.py` | **The daily totals csv link in the nav** |
| the finding that the quieter sensor is real, not a fault | `metrics.py`, `pages/sides.py` | The note under the two totals on the north v south page. |
| the date window | `data.py`, `server.py`, `pages/__init__.py`, and every page under `pages/` | **The date window at the top of every page** ![](/stages/footfall-window.png) |
| rounding the front page numbers — the newest work in the project | `metrics.py`, `pages/overview.py` | The busiest-day figure and the last-fortnight table, to the nearest 10. |

**You are done when:** you can point at a row and say what your setup calls it, and point at a part of the dashboard and say which row put it there.

## Stage 2: Find the work behind the wrong number

You have 4 minutes for this task.

Start by running `./stage 2`.

Run the command below first. It resets the project and prints the two numbers this stage is about, side by side.

    ./stage 2

**What happened:** The transport committee published a paper last year saying that the average day in 2018 saw **42,436** people walk past. The dashboard's by-year page now says **42,545** for the same year. The numbers disagree because a colleague changed the way the dashboard works out an average. Days on the project's list of unusual days, such as Grand Final Friday and Christmas, are now left out of every average, and the paper was written when every day still counted.

![The by-year page, with the 2018 row marked — its average-day number is the one that disagrees with the paper](/stages/footfall-yearly.png)

The pages open on the most recent year. Set the date window at the top to cover 2018 — the year both numbers are about.

**Your job:** Find the piece of work in the project's history that made that change. You do not have to change any code.

**You are done when:** You can name the piece of work — a commit hash, a named piece of work, or an id all count. The questions after this stage ask you which one you found. If you are not certain, choose what you have and say that you are not certain. That is more useful to us than a guess.

## Stage 3: Take that work out

You have 4 minutes for this task.

Start by running `./stage 3`.

Run the command below first. It resets the project and names the work you have to take out, so you have the name whether or not you found it in the last stage.

    ./stage 3

**What happened:** The committee never approved the change your colleague made. They want the averages to count every day the sensors recorded, including the unusual ones.

**Your job:** Take that work out of the project. Three things have to go: the list of unusual days the project keeps, the marks that flag those days on the daily and monthly charts, and the rule that leaves those days out of the averages. Everything else the dashboard shows has to keep working.

![The monthly page today: the coloured bars flag months containing an unusual day. After the removal, no bar is coloured and the note under the chart is gone.](/stages/footfall-monthly.png)

Set the date window to 2018 while you check the pages — the marks only show when the window contains an unusual day, and the pages open on the most recent year.

**You are done when:** `./check 3` says the program still runs and the by-year page reads **42,436** for 2018 again. Run it as often as you like — it does not mark you.

## Stage 4: Put it back

You have 4 minutes for this task.

Start by running `./stage 4`.

Run the command below first. It puts the project into the state where the work has already been taken out — the same state for everyone, whatever happened in the last stage.

    ./stage 4

**What happened:** The committee has changed its mind. Now that they have seen the averages with every day counted, they agree with your colleague that a public holiday when the offices are shut says nothing about how many people walk to work on an ordinary day, so those days should stay out of the averages after all.

**Your job:** Put that work back into the project, exactly as it was before the removal.

![The by-year page you are aiming for: the marked 2018 row reads its excluded-days number again](/stages/footfall-yearly.png)

**You are done when:** `./check 4` says the program still runs and the by-year page reads **42,545** for 2018 again.
