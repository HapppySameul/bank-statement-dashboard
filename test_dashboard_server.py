"""The local editing server must only answer the dashboard itself."""

import http.client
import json
import threading

import pandas as pd
import pytest

from bank_statement_dashboard import EXPECTED_COLUMNS, build_dashboard
from dashboard_server import make_server


@pytest.fixture
def server(tmp_path):
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

    httpd = make_server(tmp_path, 0)
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
