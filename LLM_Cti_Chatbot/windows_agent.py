"""
ThreatGuard AI - Local Windows Scanner Agent.

WHY THIS EXISTS: a Docker container (Linux) cannot read the Windows host registry. This tiny agent
runs natively on the Windows host, performs the read-only registry scan from `software_scanner.py`,
and serves the result to the dockerised FastAPI backend over HTTP.

Run it on Windows (no admin rights, standard library only):

    python windows_agent.py

Security model
--------------
* Binds to 127.0.0.1 by default. Docker Desktop reaches it via host.docker.internal.
* GET only; two endpoints: /health and /inventory. No write, exec or uninstall operations exist.
* Host-header allow-list (blocks DNS-rebinding from web pages).
* Optional shared secret: set SOFTWARE_AGENT_TOKEN (same value for the backend). Mandatory if you
  bind to anything other than loopback.
* Returns only application name/version/publisher/install date/install location.
  No environment variables, API keys, files or browser data are ever read or returned.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Set
from urllib.parse import urlparse

from software_scanner import scan_registry

LOG = logging.getLogger("threatguard.windows_agent")
DEFAULT_PORT = 8765
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_handler(token: str, allowed_hosts: Set[str], scan_fn=scan_registry):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ThreatGuardWindowsAgent/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # keep logs short, no headers/tokens
            LOG.info("%s - %s", self.address_string(), fmt % args)

        def _send(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in allowed_hosts:
                self._send(403, {"ok": False, "error": "host not allowed"})
                return False
            if token and not hmac.compare_digest(self.headers.get("X-Agent-Token", ""), token):
                self._send(401, {"ok": False, "error": "invalid or missing agent token"})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorised():
                return
            path = urlparse(self.path).path
            if path == "/health":
                self._send(200, {"ok": True, "agent": "threatguard-windows-agent", "platform": sys.platform,
                                 "read_only": True})
            elif path == "/inventory":
                try:
                    self._send(200, {"ok": True, "scan": scan_fn()})
                except Exception as exc:  # never crash the agent
                    LOG.exception("scan crashed")
                    self._send(500, {"ok": False, "error": f"scan error: {type(exc).__name__}"})
            else:
                self._send(404, {"ok": False, "error": "not found"})

        # Everything that could mutate state is rejected.
        def _reject(self) -> None:
            self._send(405, {"ok": False, "error": "read-only agent: GET only"})

        do_POST = do_PUT = do_DELETE = do_PATCH = _reject  # type: ignore[assignment]

    return Handler


def make_server(host: str, port: int, token: str, scan_fn=scan_registry) -> ThreadingHTTPServer:
    allowed = set(LOOPBACK) | {"host.docker.internal", "gateway.docker.internal"}
    extra = os.getenv("SOFTWARE_AGENT_ALLOWED_HOSTS", "")
    allowed |= {h.strip().lower() for h in extra.split(",") if h.strip()}
    return ThreadingHTTPServer((host, port), build_handler(token, allowed, scan_fn))


def main() -> int:
    parser = argparse.ArgumentParser(description="ThreatGuard AI read-only Windows software scanner agent")
    parser.add_argument("--host", default=os.getenv("SOFTWARE_AGENT_BIND", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SOFTWARE_AGENT_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    token = os.getenv("SOFTWARE_AGENT_TOKEN", "")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if sys.platform != "win32":
        LOG.warning("Not running on Windows: /inventory will report scan_status=failed.")
    if args.host not in LOOPBACK and not token:
        LOG.error("Refusing to bind to %s without SOFTWARE_AGENT_TOKEN set.", args.host)
        return 2

    server = make_server(args.host, args.port, token)
    LOG.info("Read-only Windows scanner agent listening on http://%s:%d (auth token: %s)",
             args.host, args.port, "required" if token else "not set")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("Shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
