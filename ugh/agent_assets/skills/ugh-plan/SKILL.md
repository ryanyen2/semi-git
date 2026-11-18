---
name: ugh-plan
description: Use when you (an AI coding agent) are about to implement a multi-step task in an ugh-tracked repo and want your intent recorded as a plan that ugh can check your work against. Teaches the declare -> save --as -> report -> done loop over the `ugh_plan` and `ugh_save` MCP tools (or `ugh plan` / `ugh save --as` in a shell). The agent declares the plan, not the human.
---

# Declaring and closing an ugh plan

In ugh, a *plan* is the list of names you declare before their edits exist, plus a baseline: HEAD
when you declared them. Each name is a piece of work you intend to build. As you build, you save
each piece under its name, and ugh reports which declared names have saves since the baseline,
which are still missing, and which steps you saved under no declared name.

The code is always the source of truth. A plan that reports a name missing, or steps outside every
name, is information about what you built, never an error to hide by renaming things.

## The loop

1. **Declare** — call `ugh_plan` with `declare: ["export", "audit log"]` (shell: `ugh plan export
   "audit log"`). Use short names a person would recognise in `ugh log`. Declaring again adds names
   to the plan and keeps its baseline.

2. **Work and save under the name** — implement one piece, then call `ugh_save` with `message` (your
   own words about what the work was) and `as` set to the declared name (shell: `ugh save -m "..."
   --as export`). The save's steps carry that name. A save without `as` carries no name, and a later
   edit to a named definition is unnamed unless it is saved `as` the name again, so pass `as` on
   every save that belongs to a declared piece.

3. **Report** — call `ugh_plan` with no arguments (shell: `ugh plan`). For each declared name it
   lists the saves since the baseline that carry it; an empty list means the name is still missing.
   `other` lists the steps since the baseline that carry no declared name, each with its address. If
   one of those belongs to a declared piece, name it: `ugh_name` with that address and the label.

4. **Done** — call `ugh_plan` with `done: true` (shell: `ugh plan --done`) when the work is finished
   or abandoned. The plan is cleared; the names on the steps stay.

**Working without a plan.** This loop is worth the ceremony for multi-step work, and most work is
not that. If you are just doing what was asked, edit, then `ugh_save` with your own words, and `as`
if the work deserves a name.

## Writing good names

- One name per piece of work someone might want to take out or bring back on its own.
- A name is a label, not a description: `export`, `rate limiting`, `audit log`. The message on each
  save carries the description.
- A token that is both a name and a git rev (a branch called `export`) is refused as ambiguous by
  every verb, so avoid branch and tag names.

## Related

- `ugh-agent` — orienting in an ugh repo before you start, what each read costs, and the line between
  your actions and the human's.
- `ugh-workflow` — choosing between the look-alike verbs once you need one.
