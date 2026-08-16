# Remote sessions: their laptop, keys, Claude Code

For when the participant runs on their own machine over a video call, which is
now the normal case.

Most of what used to be on this page is done by the website and the setup
script. `running-the-study.md` is the operator's manual. This page is what is
left: what to screen for at recruitment, and what the isolation actually
guarantees, because that is what you will be asked.

## What they need

- macOS or Linux. Windows only through WSL, so screen it out at recruitment.
- git and curl.
- A video call with screen sharing.
- Nothing else. Their Python version does not matter, and neither does whether
  they already have Claude Code.

## Their Python does not matter

`uv` downloads its own Python, so a laptop with Python 3.8, or none at all,
works. The setup script fetches 3.12 and uses only that. Do not ask them to
install or upgrade Python, and do not let them "fix" their system Python for
this.

## Their AI assistant account is not used

This is the part participants ask about, and the answer should be exact.

- The assistant runs with `CLAUDE_CONFIG_DIR` pointed at `.claude-study` inside
  the study folder. Its whole config tree lives there: settings, session
  history, project state. Their own `~/.claude` is not read and not written.
- Authentication goes through an `apiKeyHelper` inside that folder, which
  returns the key we issued. Their subscription is not touched, and they are
  never asked to approve a key, because `ANTHROPIC_API_KEY` is deliberately not
  set: setting it would make Claude Code prompt them once to approve it, which
  is friction with no benefit.
- The session shell unsets `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`, so a
  key of their own that happens to be in their environment cannot be picked up
  and billed to them.
- `DISABLE_AUTOUPDATER` is on, so the assistant cannot upgrade itself between
  participant three and participant four. The version is part of the condition.
- `study-cleanup` removes the profile and the key at the end.

If they ask what is recorded: the prompts they send, the tool calls the
assistant makes, and the commands they run inside the session shell, along with
exit codes and timings. Nothing outside the study folder. Nothing before or
after the session. It is on the consent page in those words.

## API keys

Issue keys **for the study**, cap their spend, and revoke them the day the
sessions end. Enter them once in the console under **Setup → Session keys**.

The participant never sees or pastes a key. The setup script fetches the pair
for their session using the code from their study page. That is deliberate: a
key that has to be copied by hand is a key that ends up in the wrong window.

The keys are readable by anything holding a participant link, which is the price
of that. It is why they must be study keys with a hard cap, and why the roster
has a per-participant **Revoke** button. Pressing it marks them revoked and
clears them from the participant's record; revoke them at the provider too.

## A day before

Send the link and the message in `running-the-study.md` §2. They do consent,
background and setup on their own, and you watch it land in **Live**.

Everything slow is already done in the bundle, including the history view
refresh, so their first command in the session is fast.

## Ten minutes before

The setup checklist on their page is the check. If it is green, they are ready.
If it is not, that is the conversation to have now rather than at minute four of
the first request.

Then ask them to share their screen and open `./bin/study-shell`.

## Things that go wrong

- **`claude: command not found`.** Their shell has not picked up
  `~/.local/bin`. New terminal tab, then `./bin/study-shell`.
- **`uv: command not found` after install.** Same cause, same fix.
- **`sgt: command not found`.** They are outside the session shell. Everything
  for the study runs inside `./bin/study-shell`.
- **"this isn't a git repository".** They are outside `work/`. `study-work`.
- **The setup script refuses to run.** Read what it says. If it says the folder
  is not one they are assigned, they have the wrong bundle: send the right one
  rather than overriding it. A session run from the wrong folder looks perfectly
  normal and is unusable.
- **Their link will not reopen.** Cleared cache, second browser, private window.
  Open their record in the console and press **Release link**.
- **Nothing arrives from their machine.** Their local log is complete either
  way. Ask them to run `study-sync --verbose` and read the error; if it cannot
  be fixed during the session, collect `telemetry/events.jsonl` afterwards.
- **They wedge the project.** Pause the clock with reason "tool failure", have
  them unpack a spare copy, skip to the next request. Keep one spare bundle per
  condition ready.

## After the session

- Revoke both keys, in the console and at the provider.
- Check **Hand over your data** shows both halves delivered.
- Confirm they ran `study-cleanup`. The projects get reused.
