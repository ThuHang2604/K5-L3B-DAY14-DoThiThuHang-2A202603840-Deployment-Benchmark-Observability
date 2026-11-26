"""
Chạy toàn bộ Part 7 Bước 1 (CP7) và lưu report JSON — số liệu thật cho BENCHMARK_REPORT.md.

Chạy từ gốc repo (WSL2/Linux/macOS), sau khi:
    pip install -r 06-lab-complete/benchmark-observability/develop/requirements.txt

    python scripts/run_part7_local.py

Các bước (đúng lệnh trong CHECKPOINTS.md CP7):
  1. Eval v1 (port 8001)                          → reports/eval_v1.json
  2. Eval v2 với KNOWLEDGE_BASE GỐC (port 8002)   → reports/eval_v2.json  (--baseline eval_v1)
     KB gốc lấy từ commit đề bài bằng `git show`, chạy trong thư mục tạm — không đụng code đã sửa.
  3. Eval v2 đã sửa KB                           → reports/eval_v2_fixed.json (--min-accuracy 1.0 --baseline eval_v2)
  4. Quality gate demo: eval v1 với --baseline eval_v2_fixed → in exit code (mong đợi 1)
  5. Load test v2 concurrency 1/10/50/100        → reports/load_v2_c{N}.json
  6. Load test MOCK_SLOW_RATE 0 / 0.03 / 0.1     → reports/load_v2_slow_{X}.json
  7. Copy develop/reports/*.json ra reports/ ở gốc repo

Eval chạy với MOCK_ERROR_RATE=0 để đo CHẤT LƯỢNG câu trả lời, không bị lỗi upstream ngẫu nhiên làm nhiễu.
Load test giữ cấu hình mặc định của mock (error 1%, slow 3%) trừ bảng so sánh MOCK_SLOW_RATE.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / "06-lab-complete" / "benchmark-observability" / "develop"
REPORTS = DEV / "reports"
UPSTREAM_COMMIT = "83e677f"          # commit đề bài (trước khi sửa KNOWLEDGE_BASE)
KB_PATH = "06-lab-complete/benchmark-observability/develop/utils/mock_llm.py"
PY = sys.executable


def wait_health(port: int, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.3)
    raise SystemExit(f"❌ Agent ở port {port} không lên sau {timeout}s")


class Agent:
    def __init__(self, version: str, port: int, cwd: Path, **env):
        e = os.environ.copy()
        e.update({"AGENT_VERSION": version, "PORT": str(port)})
        e.update({k: str(v) for k, v in env.items()})
        self.log = open(REPORTS / f"agent_{version}_{port}.log", "a", encoding="utf-8")
        self.proc = subprocess.Popen([PY, "app.py"], cwd=cwd, env=e,
                                     stdout=self.log, stderr=subprocess.STDOUT)
        self.port = port
        wait_health(port)
        print(f"  ▶ agent {version} port {port} {env or ''}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.log.close()


def run(args: list[str]) -> int:
    print("  $ python " + " ".join(args))
    return subprocess.run([PY, *args], cwd=DEV).returncode


def original_develop_copy() -> Path:
    """Bản sao develop/ với mock_llm.py gốc của đề (KB chưa sửa)."""
    tmp = Path(tempfile.mkdtemp(prefix="day14-v2-original-"))
    dst = tmp / "develop"
    shutil.copytree(DEV, dst, ignore=shutil.ignore_patterns("reports", "__pycache__"))
    src = subprocess.run(["git", "show", f"{UPSTREAM_COMMIT}:{KB_PATH}"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    if src.returncode != 0:
        raise SystemExit(f"❌ Không lấy được mock_llm.py gốc từ commit {UPSTREAM_COMMIT}: {src.stderr}")
    (dst / "utils" / "mock_llm.py").write_text(src.stdout, encoding="utf-8")
    return dst


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    gate_codes = {}

    print("\n=== 1–2. Eval v1 và v2 (KB gốc) ===")
    orig = original_develop_copy()
    with Agent("v1", 8001, DEV, MOCK_ERROR_RATE=0), Agent("v2", 8002, orig, MOCK_ERROR_RATE=0):
        gate_codes["eval_v1"] = run(["eval/run_eval.py", "--url", "http://localhost:8001",
                                     "--out", "reports/eval_v1.json"])
        gate_codes["eval_v2"] = run(["eval/run_eval.py", "--url", "http://localhost:8002",
                                     "--out", "reports/eval_v2.json",
                                     "--baseline", "reports/eval_v1.json"])
    shutil.rmtree(orig.parent, ignore_errors=True)

    print("\n=== 3–4. Eval v2 sau khi sửa KB + quality gate demo ===")
    with Agent("v1", 8001, DEV, MOCK_ERROR_RATE=0), Agent("v2", 8002, DEV, MOCK_ERROR_RATE=0):
        gate_codes["eval_v2_fixed"] = run(["eval/run_eval.py", "--url", "http://localhost:8002",
                                           "--min-accuracy", "1.0",
                                           "--baseline", "reports/eval_v2.json",
                                           "--out", "reports/eval_v2_fixed.json"])
        gate_codes["gate_v1_vs_v2_fixed"] = run(["eval/run_eval.py", "--url", "http://localhost:8001",
                                                 "--baseline", "reports/eval_v2_fixed.json"])

    print("\n=== 5. Load test concurrency 1/10/50/100 (v2, cấu hình mặc định) ===")
    with Agent("v2", 8002, DEV):
        for c in (1, 10, 50, 100):
            run(["load_test.py", "--url", "http://localhost:8002", "--requests", "300",
                 "--concurrency", str(c), "--out", f"reports/load_v2_c{c}.json"])

    print("\n=== 6. Load test MOCK_SLOW_RATE 0 / 0.03 / 0.1 ===")
    for rate in ("0", "0.03", "0.1"):
        with Agent("v2", 8002, DEV, MOCK_SLOW_RATE=rate):
            run(["load_test.py", "--url", "http://localhost:8002", "--requests", "300",
                 "--concurrency", "20", "--out", f"reports/load_v2_slow_{rate}.json"])

    print("\n=== 7. Copy report ra gốc repo ===")
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    for f in sorted(REPORTS.glob("*.json")):
        shutil.copy2(f, out / f.name)
        print(f"  ✓ reports/{f.name}")

    print("\nExit code các lệnh eval (0 = gate PASS, 1 = FAIL):")
    for k, v in gate_codes.items():
        print(f"  {k:<22} {v}")
    print("\nTiếp theo: python scripts/report_tables.py  → in bảng Markdown để dán vào BENCHMARK_REPORT.md")


if __name__ == "__main__":
    main()
