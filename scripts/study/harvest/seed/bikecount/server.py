"""Serves the dashboard on localhost."""
import http.server
import os

from bikecount import counts, pages

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "counts.csv")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        readings = counts.load_readings(DATA)
        days = counts.daily_totals(readings)
        if self.path == "/":
            body = pages.render_overview(readings, days)
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def serve(port=8000):
    print(f"http://localhost:{port}")
    http.server.HTTPServer(("", port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
