"""
Smoke test for the DataPulse rehearsal backend.

Uses only the Python standard library, so it runs anywhere Python 3.9+ is installed.

Usage (PowerShell or terminal):
    python scripts/smoke_test.py https://your-service.onrender.com YOUR_API_KEY
    python scripts/smoke_test.py http://localhost:8000 change-me-local-only

Optional third argument: the frontend origin to test CORS with, e.g.
    python scripts/smoke_test.py https://your-service.onrender.com KEY https://your-app.vercel.app
"""

import json
import sys
import time
import urllib.error
import urllib.request

if len(sys.argv) < 3:
    print(__doc__)
    sys.exit(2)

BASE = sys.argv[1].rstrip("/")
API_KEY = sys.argv[2]
ORIGIN = sys.argv[3] if len(sys.argv) > 3 else None

results: list[tuple[str, bool, str]] = []


def parse(raw: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw  # e.g. CORS preflight returns plain "OK"


def call(method: str, path: str, body=None, key=API_KEY, headers=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("X-API-Key", key)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, parse(resp.read().decode()), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, parse(e.read().decode()), {k.lower(): v for k, v in e.headers.items()}
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"\nCould not reach {BASE + path}: {e}")
        print("Check the URL, that the service is Live in Render, and wait for a free service to wake up.")
        sys.exit(1)


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


print(f"Testing {BASE}\n(first request may take ~1 minute if a Render free service is waking up)\n")

# 1. Health
code, body, _ = call("GET", "/health", key=None, timeout=120)
check("Health endpoint returns 200", code == 200, f"status={code}")
if code == 200:
    check("Deployed commit reported", bool(body.get("commit")), f"commit={body.get('commit')}")
    check("Knowledge-base files found", body.get("kb_files_found", 0) > 0, f"kb_files_found={body.get('kb_files_found')}")

# 2. Auth
code, _, _ = call("GET", "/items", key="wrong-key")
check("Wrong API key is rejected (401)", code == 401, f"status={code}")

# 3. Config / secrets
code, body, _ = call("GET", "/config-check")
if check("Config check returns 200", code == 200, f"status={code}"):
    missing = [k for k, v in body["env_vars_set"].items() if not v]
    check("All expected env vars are set", not missing, f"missing={missing}" if missing else "")

# 4. CRUD
code, item, _ = call("POST", "/items", {"name": "smoke-test", "description": "created by smoke test"})
if check("Create item (201)", code == 201, f"status={code}"):
    item_id = item["id"]
    code, _, _ = call("GET", f"/items/{item_id}")
    check("Read item (200)", code == 200, f"status={code}")
    code, updated, _ = call("PUT", f"/items/{item_id}", {"name": "smoke-test-updated"})
    check("Update item (200)", code == 200 and updated["name"] == "smoke-test-updated", f"status={code}")
    code, _, _ = call("DELETE", f"/items/{item_id}")
    check("Delete item (204)", code == 204, f"status={code}")
    code, _, _ = call("GET", f"/items/{item_id}")
    check("Deleted item is gone (404)", code == 404, f"status={code}")

# 5. Long-running job + polling
code, job, _ = call("POST", "/investigations", {"run_id": "smoke-7821"})
if check("Start investigation (202)", code == 202, f"status={code}"):
    job_id = job["job_id"]
    status, deadline = "queued", time.time() + 180
    while status in ("queued", "running") and time.time() < deadline:
        time.sleep(3)
        code, job, _ = call("GET", f"/investigations/{job_id}")
        status = job.get("status") if code == 200 else f"http {code}"
        print(f"       polling job {job_id}: {status}")
    check("Investigation completes", status == "completed", f"final status={status}")

# 6. CORS (optional)
if ORIGIN:
    code, _, headers = call(
        "OPTIONS", "/items", key=None,
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET",
                 "Access-Control-Request-Headers": "x-api-key,content-type"},
    )
    allowed = headers.get("access-control-allow-origin")
    check(f"CORS allows {ORIGIN}", code == 200 and allowed == ORIGIN, f"status={code}, allow-origin={allowed}")

passed = sum(1 for _, ok, _ in results if ok)
print(f"\n{passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
