# What each ugh read costs

A cost table in prose goes stale silently, and a stale one is worse than none because it teaches
confident wrong choices. So this records what decides the cost of each read rather than numbers.

- `ugh_log` is windowed to the newest 30 saves over MCP (`limit`), so it stays flat as history grows.
  Each save carries one row per step, so a save that touched many definitions is a long row.
- `ugh_show` runs the planner on the address, so its cost grows with the steps the address names and
  the later work that touches the same definitions. A one-step address is the cheapest.
- `ugh_find` reads the names, the save subjects, the definitions and the captured asks once, and
  returns at most `limit` hits (10 by default).
- `ugh_plan` reads the saves since the plan's baseline.

Latency is dominated by whether a call is the one that mines the working tree. The first ugh call in
a session pays for mining (seconds); later ones do not, so a second read is nearly free.

To measure a read, take the length of its JSON result divided by four as an approximate token count,
on a repository of the size you care about.
