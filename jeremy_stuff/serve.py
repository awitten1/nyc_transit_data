"""Simple HTTP server for local jeremy_stuff pages.

Usage:
    python serve.py
    python serve.py penn_station_clusters.html
"""
import http.server, webbrowser, os, sys

PORT = 8765
os.chdir(os.path.dirname(os.path.abspath(__file__)))
TARGET = sys.argv[1] if len(sys.argv) > 1 else "subway_od_map.html"

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence request logs

print(f"Serving at  http://localhost:{PORT}/{TARGET}")
print("Press Ctrl+C to stop.\n")
webbrowser.open(f"http://localhost:{PORT}/{TARGET}")
# Bind to 127.0.0.1 only — not accessible from other machines on the network
http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
