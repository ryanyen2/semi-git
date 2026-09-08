# Doing it with only a shell

Not every harness wires up MCP. Codex, a bare CLI runner, or a Claude Code install where the ugh MCP
server was never configured all leave you with Bash and nothing else. Everything the tools do is
reachable that way, because the MCP tools are thin wrappers over the same projection the CLI's
`--json` mode prints. This is the mapping.

## Reads

| MCP tool | Shell equivalent |
|---|---|
| `ugh_now` | `ugh now --json` |
| `ugh_show` (id) | `ugh show <sel> --json` |
| `ugh_show` (with `at`) | `ugh show <file> --at <spec> --json` |
| `ugh_log` | `ugh log --ops --json --limit 30` |
| `ugh_status` | `ugh log --summary --json` |
| `ugh_diff` | `ugh diff <ref_a> <ref_b> --json` |
| `ugh_drift` | reported in `ugh save` output; no standalone verb |
| `ugh_recall` | no CLI equivalent — MCP-only |
| `ugh_advanced_fsck` | `ugh advanced fsck --json` |

## Two argument shapes that catch people out

`ugh save` takes its message as a **flag**, not a positional: `ugh save -m "what you did"`. The
`ugh plan intake "<text>"` row above takes a bare positional, and that similarity primed the wrong
form in testing -- an agent wrote `ugh save "msg"` and got an argparse rejection.

A **whitespace-only** working-tree edit cannot be saved. No symbol changed, so ugh mines no op for it,
and `put()` then refuses with `would overwrite uncommitted changes`. The tree can only go back
(`git checkout -- <file>`). Fold such tidying into a real edit to the same file instead of trying to
record it alone.

`ugh_recall` is the one real gap. Its nearest substitute is `ugh why <symbol> --json`, which
gives you one symbol's attribution and recorded reason rather than a batch lookup across several.

## The plan loop

| MCP tool | Shell equivalent |
|---|---|
| `ugh_plan_intake` | `ugh plan intake "<text>" --claude-session <id>` |
| `ugh_checkpoint` (preview) | `ugh plan status --json --full` |
| `ugh_checkpoint` (confirm) | happens on the human's `ugh save`; settle an ambiguous one with `ugh save --resolve-plan --confirm-hollow <id> --confirm-op <id>` |
| `ugh_plan_done` | `ugh plan done <session> --claude-session <id>` |
| `ugh_plan_adopt` | `ugh plan adopt <session> --claude-session <id>` |
| — | `ugh plan resume [<session>]` — where a stalled plan stands, and how to reopen its conversation |

Pass `--claude-session` on `done` and `adopt`. It is what the ownership check reads, and without it
you are acting as an anonymous caller, which is permitted but means the check cannot protect your own
plan from another agent either.

## Two differences worth knowing

**Confirmation is not yours to skip.** The mutating collaboration verbs (`land`, `sync`,
`propose land`, `resolve`) show a consequence preview and wait for a human on an interactive
terminal. When they detect they are *not* on a terminal — which is your situation — they apply
immediately. That is deliberate, for scripts and CI, and it means running one of them from a shell
silently removes the review step a human would have got. Do not run them; hand them over.

**Exit codes are meaningful.** A verb that does not exist exits non-zero and prints the replacement
path, so a failed call tells you the right command rather than nothing. `ugh show` on an unknown id
exits 1 with the places to look. Read the exit code before parsing the output.

## Reading output as a machine

Always add `--json` when you are the reader. The human-readable form uses ANSI colour and box drawing
that costs tokens and can break your parsing. `--no-color` exists for the case where you are showing
output to a person rather than reading it yourself.
