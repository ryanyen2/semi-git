## Install the `sgt` CLI

This extension shells out to the `sgt` CLI — it doesn't bundle its own copy.

```bash
uv venv --python 3.12
uv pip install -e ".[entities,lens]"
```

If `sgt` isn't on your `PATH`, set the full path in **Settings → semi-git: Path**
(`sgt.path`).

No API key is needed for anything in this walkthrough.
