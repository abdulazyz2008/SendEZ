"""Smoke-test a built SendEZ app (standard library only, so it runs anywhere).

    python packaging/smoke_test.py <path to SendEZ executable>

Starts the packaged app with --server-only, then: login -> upload the sample Excel -> send in test
mode -> check every certificate was produced and the batch seal verifies; then a 60-student batch to
exercise parallel rendering inside the packaged app. Exits non-zero on any failure.
"""
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def main(exe: str) -> None:
    data = tempfile.mkdtemp(prefix="sendez-smoke-")
    env = {**os.environ, "SEALSEND_DATA_DIR": data, "ADMIN_PASSWORD": "smoke-test-pass", "RETRY_BASE_SECONDS": "0.01"}
    proc = subprocess.Popen([exe, "--server-only", "--port", str(PORT)], env=env)
    try:
        for _ in range(240):
            try:
                with urllib.request.urlopen(f"{BASE}/healthz", timeout=2) as r:
                    if json.loads(r.read()).get("ok"):
                        break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("App did not start")
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

        def call(path, body=None, ctype="application/json"):
            req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": ctype} if body is not None else {})
            with opener.open(req, timeout=300) as r:
                raw = r.read()
                return json.loads(raw) if r.headers.get("content-type", "").startswith("application/json") else raw

        def upload(name, payload, event):
            b = "----sendez"
            body = (f"--{b}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n{event}\r\n"
                    f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n").encode() + payload + f"\r\n--{b}--\r\n".encode()
            return call("/api/batches", body, f"multipart/form-data; boundary={b}")

        def send_and_wait(bid):
            call(f"/api/batches/{bid}/send", json.dumps({"confirm": True}).encode())
            for _ in range(600):
                b = call(f"/api/batches/{bid}")
                if not b["job"]:
                    return b
                time.sleep(0.5)
            raise SystemExit("Sending did not finish")

        call("/api/login", json.dumps({"password": "smoke-test-pass"}).encode())
        assert call("/api/templates")[0]["id"], "template gallery empty"
        png = call("/api/templates/gold-excellence/thumb.png")
        assert png[:4] == b"\x89PNG", "template thumbnail failed (fonts / PDF engine missing?)"

        b = upload("tech_fest_2026.xlsx", (ROOT / "sample" / "tech_fest_2026.xlsx").read_bytes(), "National Tech Fest 2026")
        assert b["counts"] == {"Invalid": 2, "Pending": 14, "Total": 16}, b["counts"]
        done = send_and_wait(b["id"])
        assert done["counts"] == {"Invalid": 2, "Sent": 13, "Claim": 1, "Total": 16}, done["counts"]
        assert call(f"/api/batches/{b['id']}/verify-seal", b"{}")["intact"], "seal did not verify"
        print("sample workbook: OK", done["counts"])

        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["Student Name", "Student ID", "Email", "Category"])
        for i in range(60):
            w.writerow([f"Student {chr(65 + i % 26)}{chr(97 + i // 26)}", f"P{i:03d}", f"p{i}@example.edu",
                        "Winner" if i < 3 else "Participant"])
        b = upload("bulk.csv", out.getvalue().encode(), "Bulk Fest")
        done = send_and_wait(b["id"])
        assert done["counts"] == {"Sent": 60, "Total": 60}, done["counts"]
        print("60-student parallel batch: OK")
        print("SMOKE TEST PASSED")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main(sys.argv[1])
