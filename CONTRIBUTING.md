# Contributing

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or newer.

```bash
git clone https://github.com/ryanyen2/semi-git
cd semi-git
uv venv --python 3.12
uv pip install -e ".[dev]"
```

Run the tests.

```bash
uv run pytest
```

The suite drives real git repos through subprocesses, so a full run takes several minutes. To run
one file while you work, pass its path, e.g., `uv run pytest tests/core/test_verbs.py`.

## Checking the docs

Every `sgt ...` command quoted in the README, in `docs/guide/`, or in the bundled agent skills has
to dispatch against the real CLI. Prose drifts silently because nothing executes it, and an agent
handed a command that no longer exists will try variations until it gives up. The checker catches
that.

```bash
uv run python -m scripts.check_docs_commands
```

Add `--fix` to rewrite the commands that have a computable replacement, e.g., a verb that moved
under `sgt advanced`. CI runs the checker on every push, so a rename that forgets the docs fails
there rather than reaching a user.

## Where things live

- `sgt/` is the Python package. `sgt/core/` holds the kernel, `sgt/cli/` holds the commands, and
  `sgt/mcp/` holds the MCP server.
- `sgt/agent_assets/skills/` holds the Claude Code skills. They live inside the package, not in this
  repo's `.claude/skills/`, so that `sgt init --agent` can install them from a plain
  `uv tool install` with no checkout present. If you edit a skill, run `sgt init --agent` in this
  repo to refresh your own copy.
- `editor/vscode/` is the VS Code extension. Build it with `npm ci && npm run package` from that
  directory.
- `tests/` mirrors the package layout.

## Cutting a release

A release is one tag push. Everything else is automated in
[.github/workflows/release.yml](.github/workflows/release.yml).

First, bump the version in all three places. They have to match, because the release run compares them
against the tag and stops if they disagree.

- `pyproject.toml`, the `version` field.
- `editor/vscode/package.json`, the `version` field.
- `sgt/__init__.py`, the `__version__` field.

Then commit, tag, and push.

```bash
git commit -am "release: v0.2.0"
git tag v0.2.0
git push origin main --tags
```

Pushing the tag starts four jobs.

1. `check-version` compares the tag against both version fields and fails the run if any of the
   three disagree. It runs before anything is published, because a version number can never be
   re-uploaded to PyPI once it's taken.
2. `pypi` builds the wheel and the source distribution with `uv build`, then publishes them with
   `uv publish`.
3. `vsix` builds the VS Code extension into `semi-git-<version>.vsix`.
4. `release` creates the GitHub release, writes the notes from the commits since the last tag, and
   attaches the `.vsix` file.

If a job fails partway, fix the problem and re-run the workflow from the Actions tab using "Run
workflow" with the same tag. `uv publish` refuses to overwrite a version that already exists, so a
re-run after a successful PyPI upload will fail on that step. Bump to a new patch version instead.

### Credentials

The `pypi` job reads a repository secret named `PYPI_TOKEN`. To set it, create an API token at
[pypi.org/manage/account/token](https://pypi.org/manage/account/token/) scoped to the `semi-git`
project, then add it under Settings, Secrets and variables, Actions.

You can move to PyPI trusted publishing later, which removes the token entirely. It uses GitHub's
OIDC identity instead. To switch, add a pending publisher on PyPI for this repository and the
`release.yml` workflow, add `id-token: write` to the `pypi` job's permissions, and drop the
`UV_PUBLISH_TOKEN` line.

The VS Code Marketplace is not wired up. Releases attach the `.vsix` file to the GitHub release, and
people install it with "Install from VSIX" in the Extensions view. To publish to the Marketplace
instead, register a publisher whose ID matches the `publisher` field in
`editor/vscode/package.json`, create a personal access token in Azure DevOps, add it as a
`VSCE_PAT` secret, and add a `vsce publish` step to the `vsix` job.

### Publishing by hand

If you need to publish without CI, e.g., the first release of a new package name:

```bash
uv build
UV_PUBLISH_TOKEN=... uv publish

cd editor/vscode && npm ci && npx @vscode/vsce package --no-dependencies
gh release create v0.2.0 --generate-notes editor/vscode/*.vsix
```

## Style

`CLAUDE.md` in the repo root describes the code conventions. For prose, write plainly. Use everyday
words, complete sentences, and no promotional phrasing. Documentation is for someone who has not
seen the codebase before.
