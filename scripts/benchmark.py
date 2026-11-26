#!/usr/bin/env python3
"""Burst + duplicate-retry benchmark against the live OrderFlow AWS API."""

import argparse
import concurrent.futures
import json
import os
import statistics
import time
import urllib.error
import urllib.request


def post_order(api_url: str, payload: dict) -> tuple[int, float, str]:
    url = f"{api_url.rstrip('/')}/orders"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return resp.status, elapsed_ms, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return e.code, elapsed_ms, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        return 0, elapsed_ms, str(e)


def get_stats(api_url: str, run_id: str, merchant_id: str) -> dict:
    url = f"{api_url.rstrip('/')}/stats?run_id={run_id}&merchant_id={merchant_id}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = int(round((pct / 100.0) * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--label", default="v1")
    parser.add_argument("--orders", type=int, default=50)
    parser.add_argument("--duplicates", type=int, default=15)
    parser.add_argument("--concurrency", type=int, default=25)
    parser.add_argument("--wait-seconds", type=int, default=2)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    run_id = f"{args.label}-{int(time.time())}"
    merchant_id = "merchant-acme"
    amount_cents = 2500

    payloads = [
        {
            "order_id": f"ord-{run_id}-{i:04d}",
            "merchant_id": merchant_id,
            "amount_cents": amount_cents,
            "run_id": run_id,
        }
        for i in range(args.orders)
    ]
    # Add realistic duplicate webhook retries for the first K orders
    for i in range(min(args.duplicates, args.orders)):
        payloads.append(dict(payloads[i]))

    print(f"\n=== Running {args.label.upper()} Benchmark (run_id={run_id}) ===")
    print(f"Target: {args.api_url}")
    print(f"Sending {args.orders} unique orders + {min(args.duplicates, args.orders)} duplicate retries "
          f"({len(payloads)} total requests, concurrency={args.concurrency})...")

    t_start = time.perf_counter()
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(post_order, args.api_url, p) for p in payloads]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    total_wall_s = time.perf_counter() - t_start

    status_counts: dict[int, int] = {}
    latencies = []
    for code, ms, _ in results:
        status_counts[code] = status_counts.get(code, 0) + 1
        latencies.append(ms)

    latencies.sort()
    ok_requests = sum(cnt for code, cnt in status_counts.items() if 200 <= code < 300)
    failed_requests = len(payloads) - ok_requests

    if args.wait_seconds > 0:
        print(f"Waiting up to {args.wait_seconds}s for async workers to drain...")
        deadline = time.time() + args.wait_seconds
        stats = get_stats(args.api_url, run_id, merchant_id)
        while time.time() < deadline and stats["unique_orders_stored"] < args.orders:
            time.sleep(3)
            stats = get_stats(args.api_url, run_id, merchant_id)
    else:
        stats = get_stats(args.api_url, run_id, merchant_id)

    expected_orders = args.orders
    expected_cents = args.orders * amount_cents
    stored_orders = stats["unique_orders_stored"]
    ledger_tx = stats["ledger_tx_count"]
    ledger_cents = stats["ledger_total_cents"]

    data_loss_orders = max(0, expected_orders - stored_orders)
    duplicate_ledger_tx = max(0, ledger_tx - stored_orders)
    ledger_drift_cents = ledger_cents - expected_cents

    summary = {
        "label": args.label,
        "run_id": run_id,
        "total_requests": len(payloads),
        "unique_orders_sent": expected_orders,
        "duplicate_retries_sent": min(args.duplicates, args.orders),
        "wall_time_s": round(total_wall_s, 2),
        "http_status_counts": status_counts,
        "http_2xx_rate_pct": round(100.0 * ok_requests / len(payloads), 1),
        "http_failed_requests": failed_requests,
        "latency_p50_ms": round(percentile(latencies, 50), 1),
        "latency_p95_ms": round(percentile(latencies, 95), 1),
        "latency_p99_ms": round(percentile(latencies, 99), 1),
        "expected_unique_orders": expected_orders,
        "stored_unique_orders": stored_orders,
        "data_loss_orders": data_loss_orders,
        "ledger_tx_count": ledger_tx,
        "duplicate_ledger_tx": duplicate_ledger_tx,
        "expected_ledger_cents": expected_cents,
        "actual_ledger_cents": ledger_cents,
        "ledger_drift_cents": ledger_drift_cents,
    }

    print("\n--- RESULTS ---")
    print(f"HTTP Statuses      : {status_counts}")
    print(f"API 2xx Rate       : {summary['http_2xx_rate_pct']}% ({ok_requests}/{len(payloads)})")
    print(f"API Latency        : p50={summary['latency_p50_ms']}ms | p95={summary['latency_p95_ms']}ms | p99={summary['latency_p99_ms']}ms")
    print(f"Unique Orders      : {stored_orders}/{expected_orders} stored (lost={data_loss_orders})")
    print(f"Ledger Tx Count    : {ledger_tx} (expected={expected_orders}, duplicates={duplicate_ledger_tx})")
    print(f"Ledger Total       : ${ledger_cents/100:,.2f} (expected=${expected_cents/100:,.2f}, drift=${ledger_drift_cents/100:+,.2f})")

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"Saved report -> {args.out}")


if __name__ == "__main__":
    main()

