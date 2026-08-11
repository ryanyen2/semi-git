# Narrative tree for the sgt paper

Every node below is one sentence. Nesting shows where the point sits. Page counts are a budget, not
a promise.

Thesis carried by every section: version control still stores and exposes history as lines of code,
while developers now express changes as natural language intents to AI agents, so the unit of record
and the unit of thought no longer match, and sgt records history at the level of intents so the
history a developer reads matches the history they authored.

Stance carried by every section: this is a set of trade-offs we made because of how people write
code now, and not a claim that we beat git, jujutsu, pijul, darcs, or mercurial.

---

## 1. Introduction (about 2 pages)

**1.1 The loop developers are in now.** A developer describes what they want in a sentence, an agent
produces the edit, and so the thing the developer authored is a request while the thing that landed
is code they never typed.

- 1.1.1 A working day is now a short list of things the developer asked for, and each item on that
  list arrives as a large edit spread across many files.
- 1.1.2 Agent edits are fewer, bigger, and more tangled than hand-typed edits, because an agent has
  no reason to stop where a person would have paused.
- 1.1.3 The developer remembers the requests and never held the changed lines in their head at all.

**1.2 What version control hands back.** A day later git shows the developer commits, file diffs, and
lines, and none of those is the request they made.

- 1.2.1 Opening example: an agent ran for an hour and landed three unrelated pieces of work in one
  pass, and one of them turns out to be wrong.
- 1.2.2 The developer can pick lines out of that commit by hand or cherry-pick around it, and both
  put the two pieces of work they want to keep at risk.
- 1.2.3 The gap in one line: the developer knows exactly which request was wrong and has no way to
  say so to their version control system.
- 1.2.4 Second example: there is no good moment to commit, because the agent finished several things
  at once and the commit is the only unit git offers for grouping them.

**1.3 Why this happens, and not only that it happens.**

- 1.3.1 Git records a snapshot of files and groups changes by whatever the developer happened to
  stage, so what a commit means lives in the developer's head and in the message rather than in the
  record.
- 1.3.2 That was a sound design while the person typing was the person deciding, because typing is
  slow and the grouping arrived for free as a side effect of how fast a person works.
- 1.3.3 Once an agent does the typing, deciding and typing happen at different rates and in
  different amounts, so the grouping no longer arrives for free.
- 1.3.4 No tool can recover that grouping from the lines afterwards with confidence, because what
  tied a line to a request was never written down.
- 1.3.5 State the thesis, and say that this is a mismatch between two units and not a bug in git.

**1.4 What we built.**

- 1.4.1 sgt records each edit as a change to one named function or class, keeps the set of edits that
  are currently in, and rebuilds the files from that set exactly as they are on disk.
- 1.4.2 sgt never writes code, so the developer and the agent keep working the way they already do.
- 1.4.3 Each recorded edit carries the words that caused it, so the developer can ask why a function
  exists and can remove one thing they asked for by name.
- 1.4.4 Grouping edits into features is a separate layer that sgt guesses and the developer can
  correct, and we keep it separate so that a wrong guess can never damage the record underneath.

**1.5 What we gave up to get that.**

- 1.5.1 Reading code function by function needs a parser for each language, so a file sgt cannot
  parse is tracked as one unit and gets none of the benefit.
- 1.5.2 sgt refuses to merge two versions of one function on its own and asks the developer to
  decide, which is more work than git's automatic merge and fewer silent wrong answers.
- 1.5.3 sgt keeps its own record next to git instead of replacing git, which means an ordinary repo
  underneath and two things that have to stay in step.
- 1.5.4 We say plainly that we are not better than git, jujutsu, pijul, darcs, or mercurial, and we
  name what each of them buys that we do not.

**1.6 What we want to learn.** We ask whether sgt can recover the grouping a developer actually
intended, whether developers can do the five things git makes hard, and whether the record stays
trustworthy over months of real use.

**1.7 Contributions.** We name four: the mismatch itself as an analysis, the design that follows from
it, the working system, and what we found when we used it.

---

## 2. Related work (about 2 pages)

**2.1 Programming with a language model.** Research on coding assistants has studied how people
write code with them and how people review the result, and has not asked what the change does to the
record that is left behind.

- 2.1.1 Authorship moved from typing to describing, which is the fact the rest of this paper depends
  on.
- 2.1.2 Review is now the bottleneck, and review needs a unit to review, which is exactly what git
  does not give.
- 2.1.3 The open question we take up is what history should record when a person did not type the
  code.

**2.2 What version control is for, and where git misfits.** Jackson and Perez De Rosso named the
purposes a version control system serves and the places where git's concepts fight those purposes,
and we use their method rather than inventing one.

- 2.2.1 Their six purposes give us a way to say which purpose each of our five scenarios serves
  badly.
- 2.2.2 Their idea of a misfit, meaning a place where a concept forces the user to do something the
  purpose never asked for, is the form our problem section takes.
- 2.2.3 Gitless kept git's model and fixed its surface, and we say why the mismatch we describe
  cannot be fixed at the surface.

**2.3 Distributed version control.** Mercurial and git made the same distributed bet and differ in
what they show the user, which is evidence that presentation is a design choice rather than a
consequence of the storage model.

- 2.3.1 Mercurial shows one history and infers heads, git makes the user name branches, and users
  feel that difference daily.
- 2.3.2 The lesson we take is that the unit shown to the user can be chosen, and both of them chose
  the file.

**2.4 Patch-based version control.** Darcs and pijul made the change itself the unit of record
instead of the snapshot, which is the closest anyone has come to what we want, and they kept the
line as the content of a change.

- 2.4.1 Darcs treats a change as a thing with a context and can move a change past another when the
  two do not touch, which is what lets a darcs user pull one change without its neighbours.
- 2.4.2 Darcs paid for that with merges that could take exponentially long, which is the cost of
  making the ordering of every change a first-class question.
- 2.4.3 Pijul fixed the case where git's merge gives a different answer depending on the order the
  merges happen, and it stores a conflict instead of refusing to record one.
- 2.4.4 The categorical account explains why merging is well behaved when it exists and why some
  merges do not exist at all, which is the honest reason we ask a person to decide.
- 2.4.5 What we take from this line is that a change is a better unit than a snapshot, and what we
  change is the content of a change, from a run of lines to one named function.

**2.5 Jujutsu.** Jujutsu removed the parts of git users trip on most, by committing the working copy
automatically and recording a conflict rather than blocking on it, and it still records file
contents.

- 2.5.1 Its operation log lets a user undo anything they did to the repository, which is the same
  need our undo serves and a different level from ours.
- 2.5.2 It rebases descendants for you, which is the piece we most want and which we get differently
  because our unit is smaller.

**2.6 Developers already version below the commit.** Three lines of research built tools for
versioning inside an editing session, which is evidence that the commit was never the level at which
people actually think about their changes.

- 2.6.1 Azurite let a developer undo one past edit without undoing what came after it, and it worked
  on regions of a file that move as the file changes.
- 2.6.2 The micro-versioning work recorded every small edit and let a developer bring back one of
  them, grouped by when the code was run.
- 2.6.3 Variolite let a developer keep several versions of a snippet side by side inside the editor,
  because they were exploring and the commit was the wrong size for exploring.
- 2.6.4 Read together, these three say developers version at a level git does not record, and all
  three stayed inside the editor because there was nowhere in the repository to put it.

**2.7 Untangling commits after the fact.** A body of work detects commits that mix unrelated changes
and tries to split them, and its existence is proof that the grouping problem is real and old.

- 2.7.1 Those tools reach for the same signals we do, meaning which parts of the code change
  together and what the change looks like structurally.
- 2.7.2 They work after the fact on a record that never held the intent, and our position is that
  recording the intent when it exists is easier than recovering it later.

**2.8 Tools built for the workflow people have now.** Several recent tools accept that a developer's
work does not line up with commits and rearrange what is shown, and all of them keep the line as the
unit of record.

- 2.8.1 GitButler lets a developer keep several lines of work in one working copy at once and assign
  changes between them, which treats the grouping as a first-class thing the user maintains by hand.
- 2.8.2 Graphite and GitHub's stacked pull requests make a chain of dependent changes reviewable,
  which fixes the review unit without changing the record.
- 2.8.3 Sem and similar projects try to attach meaning to commits, and they attach it alongside a
  record that is still lines.
- 2.8.4 Our difference from all of them is that we change what is stored rather than what is
  displayed, and we say what that costs.

---

## 3. Where git and the current workflow pull apart (about 2.5 pages)

**3.0 How this section works.** We walk through five things developers try to do every day, and for
each one we say what the developer wants, what git makes them do instead, which of git's concepts
forces that, and which purpose ends up served badly.

**3.1 When to commit.** The developer wants each commit to be one thing, and the agent handed them
several things at once, so any commit they make is either too big to be one thing or too early to
build.

- 3.1.1 The developer's own options are to commit a mixture, to spend twenty minutes splitting it by
  hand, or to keep working and let the mixture grow.
- 3.1.2 The concept at fault is the commit, which is asked to serve both keeping work safe and
  grouping work that belongs together, and those two pull in opposite directions once the work
  arrives in a lump.
- 3.1.3 The cost is not the twenty minutes, it is that most developers pick the mixture and the
  grouping is lost for good.

**3.2 Editing one feature when it lives in many files.** The developer wants to see and change one
feature, and git can only show them files, so they rebuild the feature in their head from a diff
every time.

- 3.2.1 Nothing in the repository records which functions belong to the same piece of work, so the
  developer's grep is the only index that exists.
- 3.2.2 This gets worse with agent-authored code, because the developer never read the code as it was
  written and has no memory to fall back on.

**3.3 Removing one thing you asked for.** The developer wants to remove one request and keep
everything else, and git can only remove commits or lines, so they operate on a unit that does not
match what they want gone.

- 3.3.1 Reverting the commit takes out the good work with the bad, and editing lines by hand puts the
  developer in the position of hand-editing code they did not write.
- 3.3.2 The developer also has to work out what else depended on the thing they are removing, and git
  does not know.
- 3.3.3 The purpose served badly here is recording coherent points, because after the surgery the
  repository holds a state nobody ever chose.

**3.4 Taking one piece of work somewhere else.** The developer wants to move one piece of work to
another branch, and cherry-pick moves a commit, so a piece of work smaller or larger than a commit
cannot be moved without hand work.

- 3.4.1 If the piece is smaller than the commit, the developer has to split the commit first, which
  is the problem from 3.1 again.
- 3.4.2 If the piece spans several commits, the developer has to find them all and get the order
  right, and getting it wrong produces a conflict that tells them nothing about the cause.

**3.5 Stacking work that builds on work not yet reviewed.** The developer wants to keep building on a
change still in review, and git ties dependent work to a branch that is going to be rewritten, so
every update to the change below rewrites everything above it.

- 3.5.1 Tools that fix this rewrite the chain for the developer, which works and leaves the developer
  with commit hashes that change under them.
- 3.5.2 The concept at fault is the branch, which couples the question of what depends on what to the
  question of who is working where.

**3.6 The five in one table.** We list each scenario against the concept that forces it and the
purpose it serves badly, in Jackson's form.

**3.7 What the five have in common.** In all five the developer knows what they want in terms of
things they asked for, and every unit git offers is defined in terms of files and lines, so every
answer is a translation the developer performs by hand.

---

## 4. What sgt is and what we traded for it (about 3 pages)

**4.1 Three commitments.** Our design follows from three decisions we made up front, and everything
else in this section is a consequence of one of them.

- 4.1.1 Record a change at the level of the thing the developer names, which is a function or a class
  and not a run of lines.
- 4.1.2 Keep the record exact, so that whatever sgt rebuilds is byte for byte what is on disk, and no
  convenience is allowed to break that.
- 4.1.3 Never guess when the answer is a decision the developer has to own, and instead show both
  sides and ask.

**4.2 The concepts, one at a time.** For each concept we say what it is, why we needed it, and what
it costs.

- 4.2.1 One edit to one symbol is our unit of record, which we chose because it is the smallest thing
  a developer names out loud, and it costs us a parser for every language we support.
- 4.2.2 The state of a codebase is the set of edits that are currently in, which we chose so that
  removing work is set arithmetic rather than text surgery, and it costs us a second thing to keep in
  step with git.
- 4.2.3 Rebuilding files from that set is exact because we store the bytes each edit produced, and the
  cost is that we store more than a diff would.
- 4.2.4 Removing an edit also removes anything built on top of it, which we do so that what is left
  still builds, and the cost is that a small removal can turn out to be large and the developer has
  to see that before agreeing.
- 4.2.5 A symbol's identity is minted once and carried across renames and moves, which we need
  because a rename is not a delete and an add, and it costs us a matching step that is sometimes
  unsure and has to say so.
- 4.2.6 Two versions of one symbol can never both be in at once, so when that happens we call it a
  fork and ask the developer to reconcile it, and the cost is real work in a case git would have
  merged silently.
- 4.2.7 Features are a grouping layer over the edits, built from how symbols change together, and we
  keep them as labels only so that a wrong grouping never touches the code.
- 4.2.8 Inside a feature we keep the points where the developer's attention moved, so the developer
  can go back to a moment in one piece of work rather than a moment in the whole repository.
- 4.2.9 A plan is intent recorded before the code exists, which we added because that is the one
  moment the intent is written down anyway, and the cost is that matching later work to a plan is a
  guess we have to show as a guess.
- 4.2.10 Git stays underneath as the store, which we chose so that leaving sgt costs nothing, and it
  costs us the ability to change anything about how git works.

**4.3 The trades, stated as trades.** Each of the following is something we are worse at than an
existing tool, and we say who it hurts.

- 4.3.1 We need a parser per language, so we are worse than git on any language we do not parse, and
  that hurts anyone whose code is mostly in one we lack.
- 4.3.2 We ask a person to resolve a fork where git would merge, so we are slower than git in the
  common case and we do not produce a merge nobody checked.
- 4.3.3 Our feature grouping is a guess, so it is worse than a hand-maintained grouping and better
  than nothing, and it needs history before it is any good.
- 4.3.4 We store more data than git does, and we think that is the right price for an exact rebuild.
- 4.3.5 We are not a collaboration model, because our unit is a change to a symbol and pijul already
  worked out what changes ought to do when many people hold them.

**4.4 How we compare, asked as user questions.** We compare sgt with git, mercurial, jujutsu, darcs,
and pijul on questions a user would ask, e.g., can I remove one thing I asked for, rather than on how
each one stores data.

---

## 5. A developer using sgt (about 2.5 pages)

**5.0 The scenario.** One developer maintains a service that is already large, works with an agent
most of the day, and shares the repository with two teammates, and every command below is one they
would have a reason to run.

**5.1 Getting set up once.** They read their existing history in and wire up the agent and the
editor, because the grouping needs history to learn from and the agent needs to know it can record
its own work.

**5.2 Saying what they are about to do.** They write the plan down before the agent starts, because
that is the only moment their intent exists in words and it costs them one command.

**5.3 Letting the agent work and recording it.** The agent runs for an hour across a dozen files, and
the save records the agent's own words, so the name of the work is written while anyone still
remembers it.

**5.4 Coming back the next morning.** They ask where they are and get what is in flight, what is
waiting on a decision, and the next thing to do, because a night of sleep costs more context than a
diff can restore.

**5.5 A bug report about one piece of that work.** They ask why the function exists, read the request
that produced it, see what removing it would cost, and remove it by name.

**5.6 Wanting part of it back.** They restore the function, hit the rule that only one version can be
in, and reconcile the two versions, and we show what sgt says rather than claiming it is smooth.

**5.7 A teammate edited the same function.** The sync brings the teammate's work in, everything else
merges without a word, the one real conflict is on one function, and the conflict cannot be closed
until the checks pass.

**5.8 Putting it up for review.** The reviewer accepts feature by feature rather than commit by
commit, which is what makes review of agent-authored work possible at all.

**5.9 Reading the history a month later.** They look at features over time and ask which request put
a given line there, and this is the payoff of everything recorded above.

**5.10 Where it was worse than git.** We name the three moments in this walkthrough where the
developer did more work than git would have asked for, and why we think the trade was right.

---

## 6. How we plan to evaluate this (about 2 pages)

**6.0 What a claim would have to survive.** We follow the two-part shape Jackson used, meaning
evidence from real repositories about whether the model holds and a study with people about whether
the model helps.

**6.1 Does the record stay exact.** We rebuild every state in a repository's history and compare it
to what git has, because if the rebuild is ever wrong then nothing else in the paper is worth
reading.

**6.2 Does the grouping match what the developer meant.** We take repositories where agent prompts
were recorded, group the edits without looking at the prompts, and measure how well our grouping
lines up with the requests the developer actually made.

**6.3 Can developers do the five things.** We run a study where the same developers attempt tasks
drawn from section 3 with git and with sgt, and we measure whether they finish, how long it takes,
and how sure they are that they got it right.

**6.4 Does it hold up over months.** We report on using sgt on its own repository for the length of
the project, including the times the grouping went wrong and what we had to change.

**6.5 What we see so far.** We report our current numbers plainly and mark which of them are early.

**6.6 What could be wrong with all of this.** We name the ways each of the four could mislead, e.g.,
that recorded prompts are not the same as intent.

---

## 7. Discussion (about 0.75 page)

**7.1 What becomes possible once the record holds intent.** Review, blame, and undo can all be asked
in the developer's own terms, and we sketch each briefly.

**7.2 What we would tell someone building the next one.** The lesson we would pass on is to record
intent at the moment it exists, because every attempt to recover it later has been harder and worse.

**7.3 What we still think git got right.** Git's storage model has held up for twenty years, and we
built on top of it rather than against it for that reason.

---

## 8. Limitations (about 0.5 page)

**8.1 The parser boundary.** Languages we cannot parse get none of this, and we say which ones we
cover today.

**8.2 The grouping needs history.** On a new repository the grouping reports one feature for
everything, and the developer has to seed it by hand.

**8.3 The intent we capture is the intent that was typed.** A developer's real reason often never
reaches the prompt, and we record the prompt.

**8.4 We have not tested this with a team.** Everything we know about collaboration in sgt comes from
small trials and from what the design implies.

---

## 9. Conclusion (about 0.25 page)

**9.1 Restate the mismatch and what we did about it.** Developers now author requests and version
control still records lines, and we built a system that records the requests, at a price we have
tried to state honestly throughout.
