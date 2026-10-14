#!/usr/bin/env python3
"""Serve the offline dashboard and persist its category edits locally."""

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from bank_statement_dashboard import DASHBOARD_SECURITY_HEADERS, update_transaction_category


class DashboardRequestHandler(SimpleHTTPRequestHandler):
    history_path: Path
    categories_path: Path
    reconciliation_path: Path
    output_path: Path
    update_lock = Lock()

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def end_headers(self) -> None:
        for name, value in DASHBOARD_SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def _allowed_hosts(self) -> set:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_is_allowed(self) -> bool:
        """Reject DNS-rebinding requests that reach localhost under another site's name."""
        if self.headers.get("Host", "").lower() in self._allowed_hosts():
            return True
        self.send_error(421, "Misdirected Request")
        return False

    def _is_same_origin_request(self) -> bool:
        """Block other websites open in the browser from editing the ledger."""
        origin = self.headers.get("Origin")
        if origin is not None:
            parsed = urlsplit(origin)
            if parsed.scheme != "http" or parsed.netloc.lower() not in self._allowed_hosts():
                return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}

    def do_GET(self) -> None:
        if not self._host_is_allowed():
            return
        if urlsplit(self.path).path not in {"/", "/index.html"}:
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        if not self._host_is_allowed():
            return
        if urlsplit(self.path).path not in {"/", "/index.html"}:
            self.send_error(404)
            return
        super().do_HEAD()

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        # The dashboard only calls the API from its own origin, so cross-origin
        # preflights are refused rather than answered with CORS headers.
        self.send_error(405)

    def do_POST(self) -> None:
        if not self._host_is_allowed():
            return
        if urlsplit(self.path).path != "/api/update-category":
            self.send_error(404)
            return
        if not self._is_same_origin_request():
            self._send_json(403, {"ok": False, "error": "Cross-origin requests are not allowed"})
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"ok": False, "error": "Content-Type must be application/json"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 16_384:
                raise ValueError("Invalid request size")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Request must be a JSON object")
            transaction_id = str(payload.get("transaction_id", "")).strip()
            category = str(payload.get("category", "")).strip()
            apply_to_future = payload.get("apply_to_future", False)
            apply_to_merchant = payload.get("apply_to_merchant", False)
            if not transaction_id or not category:
                raise ValueError("transaction_id and category are required")
            if not isinstance(apply_to_future, bool):
                raise ValueError("apply_to_future must be a boolean")
            if not isinstance(apply_to_merchant, bool):
                raise ValueError("apply_to_merchant must be a boolean")

            with self.update_lock:
                result = update_transaction_category(
                    self.history_path,
                    self.categories_path,
                    self.reconciliation_path,
                    self.output_path,
                    transaction_id,
                    category,
                    apply_to_future=apply_to_future,
                    apply_to_merchant=apply_to_merchant,
                )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # pragma: no cover - defensive server boundary
            self.log_message("Category update failed: %s", exc)
            self._send_json(500, {"ok": False, "error": "The category could not be saved; see the Terminal window"})
            return

        self._send_json(200, {"ok": True, **result})


def make_server(root: Path, port: int) -> ThreadingHTTPServer:
    handler_class = type(
        "ConfiguredDashboardRequestHandler",
        (DashboardRequestHandler,),
        {
            "history_path": root / "bank_transactions.csv",
            "categories_path": root / "merchant_categories.json",
            "reconciliation_path": root / "statement_reconciliation.json",
            "output_path": root / "index.html",
        },
    )
    handler = partial(handler_class, directory=str(root))
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve and edit the local bank statement dashboard")
    parser.add_argument("--directory", default=".", help="Dashboard folder")
    parser.add_argument("--port", type=int, default=8765, help="Local HTTP port")
    args = parser.parse_args()

    server = make_server(Path(args.directory).resolve(), args.port)
    print(f"Dashboard available at http://127.0.0.1:{args.port}/index.html")
    print("Category edits are saved to bank_transactions.csv and merchant_categories.json.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
