# What counts as legible

Written before the twelve-session harvest finished, so the surfaces are judged
against a fixed standard rather than against whatever they happen to print.

The person we are imagining has never seen this repository. They have had ten
minutes of practice on a different project. They have a clock running. They are
not going to read source code, and if the only way to answer a question is to
read source code then the surface has failed, whatever else it does well.

Four questions, one per thing the participant has to do. Each is answered from
one surface, without opening a file.

## 1. Where is the problem

They open the dashboard, compare it against the published report, and see a
number that does not match. That part is not sgt's job and should not need to be.

What sgt has to add: having seen a wrong number on a page, can they get from that
page to the work that changed it? The surfaces in play are `sgt find`, the
workbench search box, and feature blame.

  pass    a plain-English description of the symptom lands on the right work in
          the first three results
  weak    it lands there but only after the participant guesses a symbol name
  fail    the ranked list does not contain the answer, or the command needs a key
          that is not set and says nothing useful about it

## 2. Which piece of work to undo

  pass    the list of work has one entry whose label describes the change in the
          same words the task used, and the entry is distinguishable from its
          neighbours without opening any of them
  weak    the right entry is there but labelled with a raw commit subject, so it
          reads as a save rather than as a piece of work, or it is truncated
          before the distinguishing word
  fail    labels are generated names that share vocabulary with each other, or
          the work is split across entries with no indication they belong together

## 3. Which checkpoint, if not the whole thing

  pass    the checkpoints under the target carry names, and the names say what
          each one added, so `@1` versus `@2` is a real choice
  weak    checkpoints exist and are numbered but named after the commit subject,
          so choosing between them means reading commits
  fail    no checkpoints, or every checkpoint carries the feature's own name

## 4. What happens if they go ahead

This is the one that decides whether the operation is safe to attempt under a
clock, and it is the consequence pane's whole job.

  pass    before applying, the participant is told how many edits go, across how
          many symbols and files, and which other named work is affected; and the
          numbers are right
  weak    the counts are right but the affected work is named by id rather than
          by label, so knowing whether it matters means looking each one up
  fail    the preview overstates the blast radius, understates it, or the counts
          do not match what applying it actually does

## Two standing rules

**Noise counts against the surface.** A panel that answers the question and also
prints three lines of sgt's own plumbing has not passed. The participant cannot
tell which lines are theirs.

**Both arms get read the same way.** Every complaint above gets asked of
`git log`, the Source Control Graph, and the Timeline too. If a surface in the git
arm answers a question that sgt's does not, that is the finding, and it goes in
the ledger the same as any other.
