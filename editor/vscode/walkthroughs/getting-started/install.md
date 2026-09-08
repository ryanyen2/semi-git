## Install the `ugh` CLI

This extension shells out to the `ugh` CLI — it doesn't bundle its own copy.

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens]"
```

If `ugh` isn't on your `PATH`, set the full path in **Settings → ugh: Path**
(`ugh.path`).

No API key is needed for anything in this walkthrough.
