"""Serves the dashboard on localhost. Routes come from the page modules themselves."""
import http.server
import os

from footfall import data, pages

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "counts.csv")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        readings = data.load_readings(DATA)
        for module in pages.discover():
            if self.path.split("?")[0] == module.PATH:
                body = module.render(readings)
                break
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
