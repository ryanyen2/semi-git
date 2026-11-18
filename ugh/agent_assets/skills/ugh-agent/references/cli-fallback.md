# Doing it with only a shell

Not every harness wires up MCP. Codex, a bare CLI runner, or a Claude Code install where the ugh MCP
server was never configured all leave you with Bash and nothing else. Everything the tools do is
reachable that way, because the MCP tools are thin wrappers over the same projection the CLI's
`--json` mode prints. This is the mapping.

## Reads

| MCP tool | Shell equivalent |
|---|---|
| `ugh_show` | `ugh show <address> --json` |
| `ugh_find` | `ugh find "<words>" --json` |
| `ugh_log` | `ugh log --json --limit 30` |
| `ugh_check` | `ugh check --json` |
| `ugh_plan` (no arguments) | `ugh plan --json` |
| — | `ugh status --json` — counts and what needs a human |

## Writes

| MCP tool | Shell equivalent |
|---|---|
| `ugh_save` | `ugh save -m "<what you did>" [--as <name>] --json` |
| `ugh_name` | `ugh name <address> <name> --json` |
| `ugh_plan` (`declare`) | `ugh plan <name>... --json` |
| `ugh_plan` (`done`) | `ugh plan --done --json` |
| `ugh_revert` | `ugh revert <address> --json` (add `--emit` for the preview, `--whole` for the removal set) |
| `ugh_restore` | `ugh restore <address> --json` (add `--emit` for the preview) |

## Two argument shapes that catch people out

`ugh save` takes its message as a **flag**, not a positional: `ugh save -m "what you did"`. `ugh plan
<name>...` takes bare positionals, and that similarity primes the wrong form -- `ugh save "msg"`
gets an argparse rejection.

A **whitespace-only** working-tree edit cannot be saved. No definition changed, so ugh mines no step
for it, and the save refuses with `would overwrite uncommitted changes`. Fold such tidying into a
real edit to the same file instead of trying to record it alone.

## Two differences worth knowing

**Confirmation is not yours to skip.** The mutating collaboration verbs (`sync`, `resolve`) show a
consequence preview and wait for a human on an interactive terminal. When they detect they are *not*
on a terminal — which is your situation — they apply immediately. That is deliberate, for scripts
and CI, and it means running one of them from a shell silently removes the review step a human would
have got. Do not run them; hand them over.

**Exit codes are meaningful.** A verb that does not exist exits non-zero and prints the closest real
verbs. An address that names nothing or two things exits 2, with the reason and the nearest `ugh
find` hits. Read the exit code before parsing the output.

## Reading output as a machine

Always add `--json` when you are the reader. The human-readable form is laid out for a person, which
costs tokens and can break your parsing.
