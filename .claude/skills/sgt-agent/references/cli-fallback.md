# Doing it with only a shell

Not every harness wires up MCP. Codex, a bare CLI runner, or a Claude Code install where the sgt MCP
server was never configured all leave you with Bash and nothing else. Everything the tools do is
reachable that way, because the MCP tools are thin wrappers over the same projection the CLI's
`--json` mode prints. This is the mapping.

## Reads

| MCP tool | Shell equivalent |
|---|---|
| `sgt_now` | `sgt now --json` |
| `sgt_show` | `sgt show <sel> --json` |
| `sgt_log` | `sgt log --ops --json --limit 30` |
| `sgt_status` | `sgt log --summary --json` |
| `sgt_diff` | `sgt diff <ref_a> <ref_b> --json` |
| `sgt_drift` | reported in `sgt save` output; no standalone verb |
| `sgt_recall` | no CLI equivalent — MCP-only |
| `sgt_advanced_fsck` | `sgt advanced fsck --json` |

`sgt_recall` is the one real gap. Its nearest substitute is `sgt feature why <symbol> --json`, which
gives you one symbol's attribution and recorded reason rather than a batch lookup across several.

## The plan loop

| MCP tool | Shell equivalent |
|---|---|
| `sgt_plan_intake` | `sgt plan intake "<text>" --claude-session <id>` |
| `sgt_checkpoint` (preview) | `sgt plan status --json --full` |
| `sgt_checkpoint` (confirm) | happens on the human's `sgt save`; settle an ambiguous one with `sgt save --resolve-plan --confirm-hollow <id> --confirm-op <id>` |
| `sgt_plan_done` | `sgt plan done <session> --claude-session <id>` |
| `sgt_plan_adopt` | `sgt plan adopt <session> --claude-session <id>` |
| — | `sgt plan resume [<session>]` — where a stalled plan stands, and how to reopen its conversation |

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
path, so a failed call tells you the right command rather than nothing. `sgt show` on an unknown id
exits 1 with the places to look. Read the exit code before parsing the output.

## Reading output as a machine

Always add `--json` when you are the reader. The human-readable form uses ANSI colour and box drawing
that costs tokens and can break your parsing. `--no-color` exists for the case where you are showing
output to a person rather than reading it yourself.
