"""Render every page of the dashboard to plain text, one file per page.

This is how a change gets checked. Take a snapshot, do something, take another,
diff them. What moved is what the change reached, and it reads as English rather
than as a diff of html.

    python3 scripts/study/harvest/snap.py <repo> <out-dir> [query]

`query` is appended to every request, e.g. `start=2013-09-01&end=2022-09-30`. One
harvested job gave the pages a date window defaulting to the last complete year,
and that year is quiet enough that several features change nothing inside it. A
snapshot of the default view therefore reports "no change" for work that plainly
does change the app, which is the most dangerous answer a gate can give.

Pages are found by following the nav links off the front page, so this keeps
working as pages are added and does not need a hardcoded list.
"""
import html.parser
import http.client
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def free_port():
    """Ask the OS for a port nobody is using.

    This used to be a constant. A snapshot that failed to shut its server down
    left the port held, and the next snapshot then either talked to the previous
    run's app or hung waiting for a bind that would never happen. Both are worse
    than slow: one reports the wrong pages and looks like a real result.
    """
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class Text(html.parser.HTMLParser):
    """Visible text, plus the numbers inside svg bars so a chart's shape is in the diff."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.links = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        if tag == "a" and d.get("href", "").startswith("/"):
            self.links.append(d["href"])
        if tag in ("rect", "circle", "line", "path") and (d.get("height") or d.get("d")):
            self.parts.append(f"[{tag} {d.get('height') or d.get('d', '')[:40]}]")
        if tag in ("tr", "p", "div", "h1", "h2", "h3", "li", "br", "svg"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())

    def text(self):
        out = " ".join(self.parts)
        out = re.sub(r" *\n *", "\n", out)
        return re.sub(r"\n{2,}", "\n", out).strip()


def fetch(path, port):
    conn = http.client.HTTPConnection("localhost", port, timeout=30)
    conn.request("GET", path)
    r = conn.getresponse()
    body = r.read().decode("utf-8", "replace")
    conn.close()
    return r.status, body


def package_of(repo):
    """The app package inside `repo` -- whichever directory holds `server.py`.

    Found rather than passed, because the two study projects are the same shape
    under different names and a hardcoded import silently snapshots nothing on the
    one it was not written for.
    """
    for child in sorted(Path(repo).iterdir()):
        if child.is_dir() and (child / "server.py").is_file():
            return child.name
    raise SystemExit(f"no package with a server.py under {repo}")


def main(repo, out_dir, query=""):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pkg = package_of(repo)
    port = free_port()
    # stderr goes to a file, not a pipe. A pipe nobody drains fills up, the child
    # blocks writing to it, and the wait() below then never returns.
    log = tempfile.TemporaryFile()
    server = subprocess.Popen([sys.executable, "-c",
                               f"from {pkg} import server; server.serve({port})"],
                              cwd=repo, stdout=subprocess.DEVNULL, stderr=log)
    try:
        for _ in range(40):
            if server.poll() is not None:
                break
            try:
                fetch("/", port)
                break
            except OSError:
                time.sleep(0.25)
        else:
            server.kill()
            log.seek(0)
            print("the server never came up:\n" + log.read().decode()[-1500:])
            return 1
        if server.poll() is not None:
            log.seek(0)
            print("the server exited on startup:\n" + log.read().decode()[-1500:])
            return 1

        # Keyed on the path with any query string dropped. One session added a
        # date-range picker that hangs `?start=…&end=…` on every link, so keeping
        # the query in the name would rename every page the moment that session is
        # reverted, and every page would then read as both deleted and new.
        seen, queue = set(), ["/?" + query if query else "/"]
        while queue:
            path = queue.pop(0)
            bare = path.split("?")[0]
            if bare in seen:
                continue
            seen.add(bare)
            status, body = fetch(path, port)
            parser = Text()
            parser.feed(body)
            name = (bare.strip("/") or "overview").replace("/", "_")
            (out / f"{name}.txt").write_text(f"GET {path} -> {status}\n\n{parser.text()}\n")
            for link in parser.links:
                bare_link = link.split("?")[0]
                if bare_link not in seen:
                    queue.append(bare_link + "?" + query if query else link)

        print(f"{len(seen)} page(s): {', '.join(sorted(seen))}")
        return 0
    finally:
        server.kill()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        log.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else ""))
