# Các bước còn lại phải chạy trên máy (WSL2 + Docker Desktop)

Chạy trong WSL2 (`python -c "import os; print(os.name)"` phải in `posix`), từ gốc repo.

## 1. Part 6 — script chấm (CP5)
```bash
cd 06-lab-complete
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python check_production_ready.py          # bản đầy đủ, cần Docker → phải ra PASS 12/12
```
Dán phần "📋 Checklist Deliverable" vào `MISSION_ANSWERS.md` (mục Part 6).

## 2. Part 3 — deploy (CP6)
Làm theo `DEPLOYMENT.md`, chạy 4 lệnh curl, chụp `screenshots/dashboard.png`, `running.png`, `test.png`.

## 3. Part 7 Bước 1 — benchmark local (CP7)
```bash
pip install -r 06-lab-complete/benchmark-observability/develop/requirements.txt
python scripts/run_part7_local.py         # ~5 phút, ghi reports/*.json
python scripts/report_tables.py           # in bảng Markdown → dán vào BENCHMARK_REPORT.md
```

## 4. Part 7 Bước 2 — observability stack (CP8)
```bash
cd 06-lab-complete/benchmark-observability/production
P="-p k5-day14-observability"
docker compose $P up -d --build --scale agent=1
docker compose $P run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_1
docker compose $P up -d --scale agent=3 && sleep 15
docker compose $P run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_3
```
- Mở Locust web UI (http://localhost:8089, 30 user) để có tải liên tục → chụp Grafana (http://localhost:3000, admin/admin).
- Jaeger (http://localhost:16686) → Longest First → chụp trace chậm; Tags `error=true` → chụp trace lỗi; `docker compose $P logs agent | grep <trace_id>`.
- Chaos A + B cùng lúc để có ≥ 2 alert firing trong một ảnh: `cp .env.example .env`, đặt `MOCK_ERROR_RATE=0.2` và `MOCK_SLOW_RATE=0.3`, `docker compose $P up -d`, ghi giờ đổi, mở http://localhost:9090/alerts, ghi giờ firing, chụp `prometheus-alert-firing.png`. Trả `.env` về cũ sau khi xong. Làm tiếp C, D, E để điền bảng 4.2.
- Copy CSV (từ gốc repo): `cp -r 06-lab-complete/benchmark-observability/production/benchmark-results/. benchmark-results/`, rồi chạy lại `python scripts/report_tables.py` cho bảng mục 5.
- Dọn: `docker compose $P down -v`.

## 5. Trước khi nộp
- Điền hết `⟨ĐIỀN⟩` trong `MISSION_ANSWERS.md`, `DEPLOYMENT.md`, `BENCHMARK_REPORT.md`: `grep -rn "ĐIỀN" *.md`
- `git log --all --oneline -- '*.env' '.env'` không in gì.
