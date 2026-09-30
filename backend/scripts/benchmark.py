"""Measure API latency against a bulk-seeded database (python -m app.seed --bulk 50000).

Usage: python scripts/benchmark.py [base_url]
Prints median / p95 wall-clock latency per endpoint over N runs, measured from the client.
"""

import statistics
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
RUNS = 20


def token(client: httpx.Client, email: str) -> str:
    r = client.post("/api/v1/auth/token", json={"email": email, "password": "opsflow-demo"})
    r.raise_for_status()
    return r.json()["access_token"]


def timed(client: httpx.Client, headers: dict, path: str, params=None) -> tuple[list[float], dict]:
    samples, body = [], {}
    for _ in range(RUNS):
        t0 = time.perf_counter()
        r = client.get(path, params=params, headers=headers)
        samples.append((time.perf_counter() - t0) * 1000)
        r.raise_for_status()
        body = r.json()
    return samples, body


def main() -> None:
    with httpx.Client(base_url=BASE, timeout=30) as c:
        for email in ["priya@opsflow.dev", "admin@opsflow.dev"]:
            h = {"Authorization": f"Bearer {token(c, email)}"}
            # A cursor ~40 pages deep, to show keyset cost does not grow with depth
            cursor = None
            for _ in range(40):
                page = c.get(
                    "/api/v1/work-items",
                    params={"limit": 25, "cursor": cursor} if cursor else {"limit": 25},
                    headers=h,
                ).json()
                cursor = page["next_cursor"]
            cases = [
                ("list first page", "/api/v1/work-items", {"limit": 25}),
                ("list page 41 (keyset)", "/api/v1/work-items", {"limit": 25, "cursor": cursor}),
                (
                    "filter status+priority",
                    "/api/v1/work-items",
                    {"status": ["open", "blocked"], "priority": ["critical", "high"]},
                ),
                ("full-text search", "/api/v1/work-items", {"q": "refund"}),
                ("overdue by due date", "/api/v1/work-items", {"overdue": "true", "sort": "due_date"}),
                ("my work", "/api/v1/work-items", {"assignee": "me", "sort": "-priority"}),
                ("dashboard summary", "/api/v1/dashboard/summary", None),
            ]
            print(f"\n{email}")
            print(f"{'endpoint':28} {'median ms':>10} {'p95 ms':>8} {'total rows':>11}")
            for name, path, params in cases:
                samples, body = timed(c, h, path, params)
                p95 = sorted(samples)[int(0.95 * len(samples)) - 1]
                total = body.get("total", body.get("totals", {}).get("active", "-"))
                print(f"{name:28} {statistics.median(samples):10.1f} {p95:8.1f} {total!s:>11}")


if __name__ == "__main__":
    main()
