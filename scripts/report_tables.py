"""
In các bảng Markdown cho BENCHMARK_REPORT.md TỪ CHÍNH các file report đã nộp
(reports/*.json, benchmark-results/*_stats.csv) → số trong báo cáo luôn khớp file.

    python scripts/report_tables.py
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
RESULTS = ROOT / "benchmark-results"
SLO_P95_MS, SLO_ERR = 500, 0.02


def load(name):
    p = REPORTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def concurrency_table():
    print("### 2.1 Concurrency vs latency (agent v2)\n")
    print("| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |")
    print("|---|---|---|---|---|---|---|")
    for c in (1, 10, 50, 100):
        r = load(f"load_v2_c{c}.json")
        if not r:
            print(f"| {c} | (thiếu load_v2_c{c}.json) | | | | | |")
            continue
        lat = r["latency_ms"]
        ok = lat["p95"] <= SLO_P95_MS and r["error_rate"] <= SLO_ERR
        print(f"| {c} | {r['rps']} | {lat['p50']} | {lat['p95']} | {lat['p99']} | "
              f"{r['error_rate'] * 100:.2f}% | {'✅ PASS' if ok else '❌ FAIL'} |")
    print()


def slow_table():
    print("### 2.2 Tail latency\n")
    print("| `MOCK_SLOW_RATE` | avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |")
    print("|---|---|---|---|---|---|")
    for rate, label in (("0", "0"), ("0.03", "0.03 (mặc định)"), ("0.1", "0.1")):
        r = load(f"load_v2_slow_{rate}.json")
        if not r:
            print(f"| {label} | (thiếu file) | | | | |")
            continue
        lat = r["latency_ms"]
        print(f"| {label} | {lat['avg']} | {lat['p50']} | {lat['p95']} | {lat['p99']} | {lat['max']} |")
    print()


def eval_table():
    print("## 3. Benchmark chất lượng\n")
    cols = [("Agent v1", "eval_v1.json"), ("Agent v2", "eval_v2.json"),
            ("Agent v2 (sau khi sửa KB)", "eval_v2_fixed.json")]
    reps = [load(f) for _, f in cols]
    print("| Chỉ số | " + " | ".join(c for c, _ in cols) + " |")
    print("|---|" + "---|" * len(cols))

    def row(name, fn):
        print(f"| {name} | " + " | ".join(fn(r) if r else "(thiếu)" for r in reps) + " |")

    row("Accuracy", lambda r: f"{r['accuracy']:.1%} ({r['passed']}/{r['total']})")
    row("Avg tokens / câu", lambda r: str(r["avg_tokens"]))
    row("Tổng cost (USD)", lambda r: f"{r['total_cost_usd']:.6f}")
    row("Cost / câu đúng (USD)",
        lambda r: f"{r['cost_per_correct_usd']:.8f}" if r["cost_per_correct_usd"] else "n/a")
    row("Latency p50 / p95 (ms)", lambda r: f"{r['latency_ms']['p50']} / {r['latency_ms']['p95']}")
    row("Regression so với baseline",
        lambda r: str(len(r.get("quality_gate", {}).get("regressions", []))))
    row("Quality gate", lambda r: "PASS" if r.get("quality_gate", {}).get("passed") else "FAIL")
    print()
    v2 = load("eval_v2.json")
    if v2:
        print("Câu v2 (KB gốc) trả lời sai:\n")
        print("| ID | Keyword bị thiếu |")
        print("|---|---|")
        for x in v2["results"]:
            if not x["passed"]:
                print(f"| {x['id']} | {', '.join(x.get('missing_keywords', [])) or x.get('error', '')} |")
        print()


def locust_table():
    print("## 5. Benchmark scaling (Locust, 100 users, 2 phút)\n")
    print("| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |")
    print("|---|---|---|---|---|---|")
    for n in (1, 3):
        p = RESULTS / f"scale_{n}_stats.csv"
        if not p.exists():
            print(f"| {n} | (thiếu scale_{n}_stats.csv) | | | | |")
            continue
        with open(p, encoding="utf-8") as f:
            rows = {r["Name"]: r for r in csv.DictReader(f)}
        r = rows.get("/ask") or rows.get("Aggregated")
        total = int(r["Request Count"])
        fails = int(r["Failure Count"])
        print(f"| {n} | {float(r['Requests/s']):.2f} | {r['50%']} | {r['95%']} | {r['99%']} | "
              f"{fails}/{total} ({fails / total:.2%}) |")
    print("\n(Số lấy từ dòng `/ask` của CSV.)\n")


if __name__ == "__main__":
    concurrency_table()
    slow_table()
    eval_table()
    locust_table()
