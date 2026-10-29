from __future__ import annotations

"""Small local bridge for DocuDesk Web -> Canon MF440 Series eSCL.

Binds to loopback only. It exposes only health and scanner-capabilities checks
in this first milestone; scan-job creation/download will be added after this
connection test is verified.
"""

import json
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOST = "127.0.0.1"
PORT = 8765
PRINTER_HOST = "192.168.5.180"
PRINTER_PORT = 80
ALLOWED_ORIGIN = "https://lomakobodiakov.github.io"
CAPABILITIES_PATH = "/eSCL/ScannerCapabilities"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "DocuDeskLocalBridge/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        print("[DocuDesk Bridge] " + (fmt % args))

    def _headers(self, status: int, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if origin == ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if self.headers.get("Origin", "") != ALLOWED_ORIGIN:
            self._json(403, {"ok": False, "error": "Origin not allowed"})
            return
        self._headers(204)

    def do_GET(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin and origin != ALLOWED_ORIGIN:
            self._json(403, {"ok": False, "error": "Origin not allowed"})
            return

        path = urlsplit(self.path).path
        if path == "/health":
            self._json(200, {"ok": True, "service": "DocuDesk Local Bridge", "version": "0.1"})
            return
        if path != "/capabilities":
            self._json(404, {"ok": False, "error": "Not found"})
            return

        conn = http.client.HTTPConnection(PRINTER_HOST, PRINTER_PORT, timeout=8)
        try:
            conn.request("GET", CAPABILITIES_PATH, headers={"Accept": "application/xml, text/xml"})
            response = conn.getresponse()
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                self._json(502, {"ok": False, "error": "Scanner response is too large"})
                return
            if response.status != 200:
                self._json(502, {"ok": False, "error": f"Printer returned HTTP {response.status}"})
                return
            if b"ScannerCapabilities" not in data:
                self._json(502, {"ok": False, "error": "Printer response is not eSCL ScannerCapabilities XML"})
                return
            self._json(200, {
                "ok": True,
                "printer": "Canon MF440 Series",
                "address": f"http://{PRINTER_HOST}",
                "protocol": "eSCL",
                "message": "Printer responded with ScannerCapabilities XML",
                "xml": data.decode("utf-8", errors="replace"),
            })
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            self._json(502, {"ok": False, "error": f"Cannot contact scanner: {exc}"})
        finally:
            conn.close()


if __name__ == "__main__":
    print("DocuDesk Local Bridge 0.1")
    print(f"Printer: http://{PRINTER_HOST}{CAPABILITIES_PATH}")
    print(f"Local endpoint: http://{HOST}:{PORT}")
    print("Keep this window open while using the scanner test. Close it to stop the bridge.")
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nDocuDesk Local Bridge stopped.")
