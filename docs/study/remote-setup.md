# Remote sessions: their laptop, keys, Claude Code

For when the participant runs on their own machine over a video call.

## What they need

- macOS or Linux. Windows is only supported through WSL, so screen it out at
  recruitment.
- git.
- A video call with screen sharing they can give you control of, or at least
  share.
- Nothing else. Their Python version does not matter, see below.

## Their Python does not matter

`uv` downloads its own Python, so a laptop with Python 3.8, or none at all,
works. The install script fetches 3.12 and uses only that. Do not ask them to
install or upgrade Python, and do not let them "fix" their system Python for
this.

## A day before

Build one bundle per half:

```bash
scripts/make-study-bundle.sh p07 sgt coursecraft
```

That produces `~/study/bundles/p07.tgz`, about 3 MB. It holds the project copy,
the handouts, and a wheel of the exact sgt build we are testing. It does not
hold an API key.

Send it and this message:

> Before our session, please unpack the attached file somewhere easy to find,
> then run `install/setup.sh` inside it. It takes a few minutes and downloads
> its own Python, so it won't touch anything else on your machine. It should
> finish by printing "38 passed". Tell me if it doesn't and we'll sort it out
> before the session rather than during it.

Everything slow is already done in the bundle, including the history view
refresh, so their first command in the session is fast.

## API keys

The sgt half needs an OpenAI key for the plain English commands. The git half
needs no key.

- Issue a key for the study, not your personal one.
- Cap its spend. A session uses a few cents.
- Send it at the start of the session, not with the bundle, over the call rather
  than by email.
- Have them paste it into `work/.env` as one line:
  `OPENAI_API_KEY=sk-...`
- Revoke it the moment the session ends. Put this on your end of session
  checklist, because a key that lives in twelve people's home directories is a
  key you no longer control.

If it is missing or wrong, sgt still runs. Features get short generic names and
plain English selections stop working, which changes what you are measuring, so
check it before you start:

```bash
cd work && ../bin/sgt log --refresh
```

Real names in the output mean the key works.

## Claude Code

Both halves need an assistant, and it has to be the same one for everybody.

Give each participant an Anthropic API key for the session:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
claude
```

Same rules as the other key. Issue it for the study, cap it, send it at the
start, revoke it at the end.

Do not share a personal Claude account login. Accounts are per person, sharing
one breaks Anthropic's terms, and it puts your own history and billing on
someone else's laptop. If you would rather not issue keys at all, the
alternative is asking participants to use their own Claude account and
reimbursing them, but then the assistant's model and settings vary between
people, and you have to record which version each person had.

Whichever route you take, write down for each participant which model answered
them. It is part of the condition.

## Ten minutes before the session

Ask them to share their screen and check, in order:

- `cd` into the unpacked folder, then `ls`. They should see `work`, `tasks.md`,
  `tutorial.md`.
- `cd work && .venv/bin/python -m pytest -q` prints 38 passed.
- sgt half only: `../bin/sgt now` prints a short summary.
- `claude` starts and answers "hello".
- Their editor is open on `work/`.

Then hand over `00-welcome.md`.

## Things that go wrong

- **`sgt: command not found`.** They are typing `sgt`. It is `./bin/sgt` from
  the folder, or `../bin/sgt` from inside `work/`. Tell them once, at the start.
- **"this isn't a git repository".** They are outside `work/`. `cd work`.
- **`uv: command not found` after install.** Their shell hasn't picked it up.
  `export PATH="$HOME/.local/bin:$PATH"`, or a new terminal tab.
- **Tests fail during setup.** Do not run the session. Rebuild the bundle and
  check it yourself first.
- **The tool is slow on first use.** It shouldn't be, because the bundle is
  pre-refreshed. If it is, their copy didn't come from the bundle.
- **They wedge the project.** Note the time, have them unpack a spare bundle,
  skip to the next request, and mark that request stopped by a tool failure.
  Always have one spare bundle per condition ready.

## After the session

- Revoke both keys.
- Ask them to send `notes/` and the assistant transcript, then delete the folder.
- Confirm they deleted it. The projects get reused with other participants.
