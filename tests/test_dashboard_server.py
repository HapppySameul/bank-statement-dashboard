"""The local editing server must only answer the dashboard itself."""

import http.client
import json
import threading
import time

import pandas as pd
import pytest

from bank_statement_dashboard import EXPECTED_COLUMNS, build_dashboard
from dashboard_server import DelayedPublisher, make_server


class RecordingPublisher:
    def __init__(self):
        self.scheduled = 0

    def schedule(self):
        self.scheduled += 1


@pytest.fixture
def publisher():
    return RecordingPublisher()


@pytest.fixture
def server(tmp_path, publisher):
    row = {column: "" for column in EXPECTED_COLUMNS}
    row.update(
        transaction_id="t1",
        date="2026-01-01",
        statement_start="2026-01-01",
        statement_end="2026-01-31",
        merchant="Example Cafe",
        debit=4.5,
        credit=0.0,
        kind="expense",
        category="Uncategorised",
        review_status="review",
        confidence=0.0,
    )
    frame = pd.DataFrame([row])
    frame.to_csv(tmp_path / "bank_transactions.csv", index=False)
    (tmp_path / "statement_reconciliation.json").write_text("[]", encoding="utf-8")
    build_dashboard(frame, tmp_path / "index.html", [])
    (tmp_path / "README.md").write_text("not served", encoding="utf-8")

    httpd = make_server(tmp_path, 0, publisher)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], tmp_path
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, host=None, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    all_headers = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    connection.request(method, path, body=body, headers=all_headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response, payload


def category_update(port, **headers):
    body = json.dumps({"transaction_id": "t1", "category": "Dining & Takeaway"})
    return request(
        port,
        "POST",
        "/api/update-category",
        headers={"Content-Type": "application/json", **headers},
        body=body,
    )


def test_dashboard_is_served_with_privacy_headers(server):
    port, _ = server
    response, body = request(port, "GET", "/index.html")
    assert response.status == 200
    assert b"Example Cafe" in body
    assert response.getheader("Cache-Control") == "no-store, private"
    assert response.getheader("X-Frame-Options") == "DENY"
    assert "frame-ancestors 'none'" in response.getheader("Content-Security-Policy")


def test_only_the_dashboard_file_is_served(server):
    port, _ = server
    for path in ("/README.md", "/bank_transactions.csv", "/statement_reconciliation.json"):
        assert request(port, "GET", path)[0].status == 404


def test_dns_rebinding_host_is_rejected(server):
    port, _ = server
    response, body = request(port, "GET", "/index.html", host=f"attacker.example:{port}")
    assert response.status == 421
    assert b"Example Cafe" not in body


def test_same_origin_category_edit_is_saved(server):
    port, root = server
    response, body = category_update(port, Origin=f"http://127.0.0.1:{port}", **{"Sec-Fetch-Site": "same-origin"})
    assert response.status == 200, body
    assert pd.read_csv(root / "bank_transactions.csv").loc[0, "category"] == "Dining & Takeaway"


def test_cross_site_category_edit_is_rejected(server):
    port, root = server
    response, _ = category_update(port, Origin="https://attacker.example", **{"Sec-Fetch-Site": "cross-site"})
    assert response.status == 403
    response, _ = request(
        port,
        "POST",
        "/api/update-category",
        headers={"Content-Type": "text/plain", "Origin": f"http://127.0.0.1:{port}"},
        body=json.dumps({"transaction_id": "t1", "category": "Gifts"}),
    )
    assert response.status == 415
    assert request(port, "OPTIONS", "/api/update-category")[0].status == 405
    assert pd.read_csv(root / "bank_transactions.csv").loc[0, "category"] == "Uncategorised"


def test_saved_edit_schedules_a_publish(server, publisher):
    port, _ = server
    category_update(port, Origin="https://attacker.example", **{"Sec-Fetch-Site": "cross-site"})
    assert publisher.scheduled == 0
    response, body = category_update(port, Origin=f"http://127.0.0.1:{port}", **{"Sec-Fetch-Site": "same-origin"})
    assert response.status == 200, body
    assert publisher.scheduled == 1


def test_a_burst_of_edits_publishes_once(tmp_path):
    marker = tmp_path / "runs"
    script = tmp_path / "launcher.sh"
    script.write_text(f'echo "$1" >> "{marker}"\n', encoding="utf-8")
    delayed = DelayedPublisher(["/bin/sh", str(script), "publish-if-changed"], 0.2)
    for _ in range(3):
        delayed.schedule()
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.4)
    assert marker.read_text(encoding="utf-8").splitlines() == ["publish-if-changed"]
