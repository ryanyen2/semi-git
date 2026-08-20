# Remote sessions: running on the participant's laptop

This page covers what you need to know when the participant runs the study on
their own machine over a video call, which is the normal case.

Most of the setup is handled automatically by the study website and the setup
script. The step-by-step guide for running sessions is in
`running-the-study.md`. This page covers what is left: what to check when
recruiting, and what the isolation guarantees — because participants will ask.

## What the participant needs

- **macOS or Linux.** Windows works only through WSL (Windows Subsystem for
  Linux), so screen for this at recruitment.
- **git and curl** installed.
- **A video call with screen sharing.**
- **Nothing else.** Their Python version does not matter, and it does not matter
  whether they already have Claude Code installed.

## Their Python version does not matter

The setup script uses `uv` (a Python package manager) to download Python 3.12
automatically. A laptop with Python 3.8, or no Python at all, works fine. Do
not ask the participant to install or upgrade Python, and do not let them try to
"fix" their system Python for this.

## Their AI assistant account is not used

This is the part participants ask about most often. The answer should be exact,
so here it is in full:

- **Isolated configuration.** The assistant runs with `CLAUDE_CONFIG_DIR`
  pointed at `.claude-study` inside the study folder. Its entire configuration
  tree lives there: settings, session history, and project state. The
  participant's own `~/.claude` directory is never read and never written to.

- **Study-issued API key.** Authentication goes through an `apiKeyHelper` file
  inside that folder, which returns the API key we issued for the study. The
  participant's own subscription is not touched, and they are never prompted to
  approve a key. (The environment variable `ANTHROPIC_API_KEY` is deliberately
  not set: setting it would cause Claude Code to prompt them once to approve it,
  which is unnecessary friction.)

- **Their own keys are blocked.** The session shell unsets `ANTHROPIC_API_KEY`,
  `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`, and `OPENAI_BASE_URL`, so any API
  key the participant happens to have in their environment cannot be
  accidentally picked up and billed to them.

- **No auto-updates.** `DISABLE_AUTOUPDATER` is set, so the assistant cannot
  upgrade itself between sessions. The assistant version is part of the
  experimental condition and must stay constant.

- **Clean removal.** `study-cleanup` removes the profile and the key at the end
  of the session.

**If the participant asks what is recorded:** the prompts they send to the
assistant, the tool calls the assistant makes, and the commands they run inside
the session shell — along with exit codes and timings. Nothing outside the study
folder. Nothing before or after the session. This is stated on the consent page
in those words.

## API keys

Issue API keys **specifically for the study**, set a hard spend cap on each, and
revoke them the day the sessions end. Enter them once in the console under
**Setup → Session keys**.

The participant never sees or pastes a key. The setup script fetches the key
pair for their session automatically, using the participant code from their study
page. This is deliberate: a key that has to be copied by hand is a key that ends
up pasted into the wrong window.

The trade-off is that the keys are readable by anything that has a participant
link. This is why they must be study-specific keys with a hard spend cap, and
why the roster has a per-participant **Revoke** button. Pressing it marks the
keys as revoked and clears them from the participant's record. Revoke them at
the API provider (Anthropic / OpenAI) as well.

## A day before the session

Send the participant their link along with the message from
`running-the-study.md` §2. They complete the consent form, background
questionnaire, and setup step on their own. You can watch their progress in the
**Live** tab of the console.

Everything that takes time is already done in the pre-built bundle, including
the sgt history view refresh. The participant's first command in the session
will be fast.

## Ten minutes before the session

The setup checklist on the participant's page is the readiness check. If every
item is green, they are ready. If anything is red, that is the conversation to
have now — not four minutes into the first request.

Then ask them to share their screen and open the session shell:

```bash
./bin/study-shell
```

## Common problems and fixes

- **`claude: command not found`.** The participant's shell has not picked up
  `~/.local/bin` where Claude Code was installed. Fix: open a new terminal tab,
  then run `./bin/study-shell` again.

- **`uv: command not found` after install.** Same cause, same fix.

- **`sgt: command not found`.** They are outside the session shell. Everything
  for the study must run inside `./bin/study-shell`.

- **"This isn't a git repository."** They have navigated outside the `work/`
  directory. Fix: type `study-work` to return to the project.

- **The setup script refuses to run.** Read what it says. If it says the folder
  does not match their assignment, they have the wrong bundle. Send them the
  correct one rather than overriding the check. A session run from the wrong
  bundle looks perfectly normal but produces unusable data.

- **Their link will not reopen.** This happens when they clear their browser
  cache, switch browsers, or open a private/incognito window. Open their record
  in the console and press **Release link**.

- **Nothing arrives from their machine.** The local log on their disk is
  complete regardless. Ask them to run `study-sync --verbose` and read the
  error. If it cannot be fixed during the session, collect the file
  `telemetry/events.jsonl` from their machine afterwards — it can be imported
  later.

- **They wedge the project.** Pause the clock with reason "tool failure", have
  them unpack a spare copy of the bundle, and skip to the next request. Keep one
  spare bundle per condition ready.

## After the session

- [ ] Revoke both API keys — in the console and at the provider.
- [ ] Check that **Hand over your data** shows both halves as delivered.
- [ ] Confirm the participant ran `study-cleanup`. The study projects may be
  reused.
