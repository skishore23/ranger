#!/usr/bin/env python3
"""Simple HTTP server to serve Ranger Studio UI mockups."""

import http.server
import socketserver
import webbrowser
from pathlib import Path

PORT = 8000
DIRECTORY = Path(__file__).parent / "ui"


class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom handler to serve from ui directory."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIRECTORY), **kwargs)

    def end_headers(self):
        """Add CORS headers for development."""
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()


def main():
    """Start the server."""
    with socketserver.TCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print("=" * 60)
        print("🎨 Ranger Studio UI Server")
        print("=" * 60)
        print(f"\n✓ Server running at: http://localhost:{PORT}")
        print(f"\n📄 Available pages:")
        print(f"   • Canvas:  http://localhost:{PORT}/canvas.html")
        print(f"   • Builder: http://localhost:{PORT}/builder.html")
        print("\n💡 Press Ctrl+C to stop the server\n")
        print("=" * 60)
        
        # Open browser automatically
        webbrowser.open(f"http://localhost:{PORT}/canvas.html")
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n👋 Server stopped. Goodbye!")


if __name__ == "__main__":
    main()

