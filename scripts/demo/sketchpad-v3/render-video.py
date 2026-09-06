#!/usr/bin/env python3
"""Render the sketchpad-v3 take as a narration-free demo GIF, from the real commands.

    python3 scripts/demo/sketchpad-v3/render-video.py [<out-dir>]

Stages a throwaway take (stage.sh), runs the four shots of RUNBOOK.md against it, and turns
every step into a frame: a title card, the terminal as it printed (left), the app as it
looked (right). Frames are written as PNGs and assembled into demo.gif with PIL, so the
only tools it needs are the ones the preflight already uses (Chrome, node, vite, sgt).
The narrated recording follows RUNBOOK.md; this is the version that needs no camera.
"""
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SGT = os.environ.get("SGT", str(ROOT / ".venv" / "bin" / "sgt"))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DRIVE = HERE.parent / "drive-page.mjs"
PORT = int(os.environ.get("PORT", "5521"))
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sketchpad-v3-video")
W, H = 1600, 900  # frame size; terminal pane is the left half
ANSI = re.compile(r"\x1b\[[0-9;]*m")

CSS = """
html,body{margin:0;background:#0f1114;color:#e6e6e6;font-family:'IBM Plex Mono',Menlo,monospace}
pre{margin:0;padding:24px 22px;font-size:11.5px;line-height:1.45;white-space:pre-wrap;word-break:break-all}
.cmd{color:#e0a94a}.dim{color:#8a8f96}
.card{height:100vh;display:flex;flex-direction:column;justify-content:center;padding:0 120px;box-sizing:border-box}
.card .step{color:#4fb3a6;font-size:22px;letter-spacing:.08em;text-transform:uppercase;margin-bottom:18px}
.card h1{font-size:44px;font-weight:600;margin:0 0 28px;line-height:1.2}
.card p{font-size:24px;line-height:1.5;color:#c9ccd1;margin:0;max-width:1200px}
"""


def sh(cmd, cwd):
    """Run a shell command in the take; return what it printed, colours stripped."""
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return ANSI.sub("", r.stdout + r.stderr)


def render_html(body, png, width, height):
    """Print an HTML fragment to a PNG of exactly width x height with headless Chrome."""
    page = OUT / (png.stem + ".html")
    page.write_text(f"<!doctype html><meta charset=utf-8><style>{CSS}</style>{body}")
    png.unlink(missing_ok=True)
    # Chrome writes the screenshot and then stays up (new headless, another Chrome running), so
    # wait for the file rather than for the process, then put the process down ourselves.
    proc = subprocess.Popen([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
                             f"--user-data-dir={OUT / 'chrome-render'}", f"--screenshot={png}",
                             f"--window-size={width},{height}", page.as_uri()],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        if png.exists() and png.stat().st_size > 0:
            break
        time.sleep(0.1)
    time.sleep(0.3)
    proc.terminate()
    proc.wait(timeout=10)
    if not png.exists():
        raise RuntimeError(f"Chrome wrote no screenshot for {page}")
    return png


def app_shot(png):
    """Photograph the running app on sheet 2."""
    plan = OUT / "plan.json"
    plan.write_text(json.dumps([{"wait": 1500}, {"shot": str(png)}]))
    subprocess.run(["node", str(DRIVE), f"http://localhost:{PORT}/", str(plan)], check=True,
                   capture_output=True,
                   env={**os.environ, "DRIVE_PORT": os.environ.get("DRIVE_PORT", "9466"),
                        "DRIVE_PROFILE": str(OUT / "chrome-drive")})
    return png


frames = []  # (PIL image, seconds)


def title(n, step, heading, line, seconds=3.0):
    png = render_html(f"<div class=card><div class=step>{step}</div><h1>{html.escape(heading)}</h1>"
                      f"<p>{html.escape(line)}</p></div>", OUT / f"{n:02d}-title.png", W, H)
    frames.append((Image.open(png).convert("RGB"), seconds))


def frame(n, command, output, app_png, seconds=6.0):
    """Terminal on the left (the command in amber, then what it printed), the app on the right."""
    body = f"<pre><span class=cmd>$ {html.escape(command)}</span>\n{html.escape(output.rstrip())}</pre>"
    term = Image.open(render_html(body, OUT / f"{n:02d}-term.png", W // 2, H)).convert("RGB")
    canvas = Image.new("RGB", (W, H), "#0f1114")
    canvas.paste(term, (0, 0))
    if app_png is not None:
        app = Image.open(app_png).convert("RGB")
        app = app.resize((W // 2 - 40, int(app.height * (W // 2 - 40) / app.width)))
        canvas.paste(app, (W // 2 + 20, (H - app.height) // 2))
    frames.append((canvas, seconds))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # stage.sh leaves vite running, and a pipe to it would wait for vite; a file does not.
    with open(OUT / "stage.log", "w") as log:
        subprocess.run(["bash", str(HERE / "stage.sh"), str(OUT / "take")], check=True,
                       stdout=log, stderr=log, env={**os.environ, "PORT": str(PORT), "SGT": SGT})
    take = (OUT / "stage.log").read_text().splitlines()[0]
    try:
        base = app_shot(OUT / "app-base.png")
        title(0, "sketchpad · sgt", "A program an agent wrote, and one idea in it",
              "Twenty commits, one per request. Mira wants to change how the hexagon groups are fastened.")
        title(1, "step 1", "How sgt presents the history",
              "Git prints the requests in order. sgt reads the same history as features, one row per concern, grouped into subsystems.")
        frame(2, "git log --oneline | head -20", sh("git log --oneline | head -20", take), base)
        frame(3, "sgt log --map", sh(f"{SGT} log --map", take), base, 8.0)
        title(4, "step 2", "Finding the feature that needs the edit",
              "Mira describes what they see. The third hit is the request that fastened the groups by their corners.")
        frame(5, 'sgt find "hexagon groups held together at their corners"',
              sh(f'{SGT} find "hexagon groups held together at their corners"', take), base, 7.0)
        frame(6, "sgt show c69fc3e", sh(f"{SGT} show c69fc3e", take), base, 6.0)
        frame(7, 'sgt show "fastened at the corners"', sh(f'{SGT} show "fastened at the corners"', take), base, 6.0)
        title(8, "step 3", "Asking the agent to make the change, and watching it land",
              (HERE / "request.txt").read_text().strip())
        frame(9, 'sgt plan intake "…"', sh(f'{SGT} plan intake "$(cat {HERE}/plan.txt)"', take), base, 5.0)
        sh(f"git apply {HERE}/agent-seam.patch && rm -f tsconfig.tsbuildinfo && npx tsc --noEmit", take)
        diff = sh("git diff --stat", take)
        echo = sh(f'{SGT} save -m "set the fastened hexagon groups apart by a seam"', take)
        seam = app_shot(OUT / "app-seam.png")
        frame(10, 'sgt save -m "set the fastened hexagon groups apart by a seam"',
              diff + "\n" + echo, seam, 8.0)
        frame(11, 'sgt show "fastened at the corners"', sh(f'{SGT} show "fastened at the corners"', take), seam, 6.0)
        title(12, "step 4", "Taking the fastening out and seeing the program without it",
              "The cost is stated before anything changes: thirteen edits in two files, twenty-four features untouched.")
        frame(13, 'sgt revert "fastened at the corners"', sh(f'{SGT} revert "fastened at the corners"', take), seam, 7.0)
        applied = sh(f'{SGT} revert "fastened at the corners" --yes', take)
        out = app_shot(OUT / "app-unfastened.png")
        frame(14, 'sgt revert "fastened at the corners" --yes', applied, out, 8.0)
        undone = sh(f"{SGT} undo", take)
        back = app_shot(OUT / "app-restored.png")
        frame(15, "sgt undo", undone, back, 6.0)
        title(16, "sketchpad · sgt", "Restored, byte for byte",
              "One repository, one branch. Three versions of the program, one of them held by no commit.", 4.0)
    finally:
        subprocess.run(["pkill", "-f", f"vite --port {PORT}"], capture_output=True)

    gif = OUT / "demo.gif"
    imgs = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f, _ in frames]
    imgs[0].save(gif, save_all=True, append_images=imgs[1:], loop=0,
                 duration=[int(s * 1000) for _, s in frames], optimize=True)
    for i, (f, _) in enumerate(frames):
        f.save(OUT / f"frame-{i:02d}.png")
    print(f"{gif}  {gif.stat().st_size // 1024} KB  {len(frames)} frames  "
          f"{sum(s for _, s in frames):.0f} s")


if __name__ == "__main__":
    main()
