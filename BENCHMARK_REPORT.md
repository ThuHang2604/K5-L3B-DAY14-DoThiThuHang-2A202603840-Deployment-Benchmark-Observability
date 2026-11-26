# Benchmark & Observability Report — Day 14

> Mọi con số lấy từ `reports/*.json`, `benchmark-results/*.csv` và screenshot thật.
> Các bảng số sinh tự động bằng `python scripts/report_tables.py` (đọc đúng các file đã nộp) rồi dán vào đây.

**Họ tên:** Đỗ Thị Thu Hằng
**Ngày chạy:** 2026-11-26 (Part 7 Bước 1 lúc 23:45–23:52 GMT+7)
**Môi trường:** Windows 11 Home · Intel Core i5-1155G7 @ 2.50GHz (4 nhân / 8 luồng) · 15.8 GB RAM · WSL2 Ubuntu (kernel 6.18.58.1-microsoft-standard-WSL2, thấy 8 CPU / 7.6 GiB) · Python 3.12.16 · Docker Desktop 4.99.0 (Engine 29.9.3, VM 8 CPU / 7.6 GiB). Load test và agent chạy cùng một máy (localhost).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | LLM mock mất ~120–180 ms + retrieval ~20 ms; 500 ms cho dư ~2,5 lần cho jitter và hàng đợi mà người dùng chat vẫn thấy "nhanh". Đặt theo p95 để bắt được nhóm user bị chậm mà average che mất |
| Error rate `/ask` | < 2 % | Mock có ~1 % lỗi upstream ngẫu nhiên (giống OpenAI/Anthropic thỉnh thoảng trả 503) → để error budget gấp đôi mức nền; vượt 2 % là có sự cố thật. Alert đặt lỏng hơn (5 % trong 1 phút) để tránh báo động vì spike ngắn |
| Accuracy trên golden dataset | ≥ 100 % (23/23), 0 regression | Golden dataset nhỏ và cố định; bản deploy không được trả lời sai câu nào mà bản trước trả lời đúng. Dùng làm quality gate trong CI (`--min-accuracy 1.0 --baseline`) |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

Lệnh: `python scripts/run_part7_local.py` (300 request mỗi mức, agent v2, cấu hình mock mặc định).

### 2.1 Concurrency vs latency (agent v2)

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.88 | 193.9 | 228.0 | 1714.1 | 1.67% | ✅ PASS |
| 10 | 35.8 | 195.7 | 222.1 | 1714.5 | 1.33% | ✅ PASS |
| 50 | 113.13 | 210.2 | 310.8 | 1746.1 | 0.67% | ✅ PASS |
| 100 | 66.7 | 362.0 | 2363.6 | 2656.9 | 0.67% | ❌ FAIL |

Nguồn: `reports/load_v2_c{1,10,50,100}.json`. Lỗi đều là `503` do mock LLM giả lập lỗi upstream (5, 4, 2, 2 request trên 300).

**Nhận xét:**
- Từ c=1 đến c=50, p50 gần như không đổi (193.9 → 195.7 → 210.2 ms) trong khi RPS tăng 3.88 → 35.8 → 113.13. Mỗi request chủ yếu là *chờ* mock LLM (`asyncio.sleep`), nên một event loop phục vụ được nhiều request cùng lúc. Theo Little's law, RPS ≈ concurrency / latency trung bình: c=1 → 1 / 0.260 s ≈ 3.8 (đo được 3.88). Ở c=10 và c=50, RPS đo được (35.8 và 113.13) thấp hơn ước tính (41.7 và 182) vì mỗi lần chạy chỉ có 300 request: thời gian khởi động và đợt request cuối chiếm tỉ lệ lớn trong `duration_s` (8.38 s và 2.65 s).
- c=100 là điểm bão hoà: RPS **giảm** từ 113.13 xuống 66.7, p50 tăng lên 362.0 ms, **p90 = 2207.9 ms**, p95 = 2363.6 ms (vượt SLO 500 ms). Chỉ 3 % request bị mock làm chậm, nên p90 lên 2,2 s không thể giải thích bằng request "slow". Nguyên nhân là request phải **xếp hàng** trong một process uvicorn duy nhất (1 event loop lo nhận kết nối, parse JSON, middleware đo metrics), cộng thêm việc load generator chạy cùng máy cũng tranh CPU. Ngay cả min latency cũng tăng từ 157.7 ms (c=50) lên 199.0 ms (c=100).
- Kết luận: một instance v2 giữ được SLO p95 < 500 ms tới khoảng 50 request đồng thời (~113 req/s trên máy này). Muốn chịu c=100 thì phải tăng số worker/instance (scale ngang) chứ không tăng concurrency trên cùng một process.
- Error rate ở mọi mức (0.67–1.67 %) đều < 2 %, đúng với mức nền 1 % lỗi upstream của mock; tải cao không làm tăng lỗi, chỉ làm tăng latency.

### 2.2 Tail latency

| `MOCK_SLOW_RATE` | avg (ms) | p50 (ms) | p95 (ms) | p99 (ms) | max (ms) |
|---|---|---|---|---|---|
| 0 | 190.2 | 190.1 | 220.2 | 225.6 | 233.1 |
| 0.03 (mặc định) | 240.9 | 193.8 | 253.6 | 1709.4 | 1739.3 |
| 0.1 | 315.0 | 193.7 | 1693.8 | 1715.3 | 1723.6 |

Nguồn: `reports/load_v2_slow_{0,0.03,0.1}.json` (300 request, concurrency 20). RPS lần lượt 101.44 / 62.57 / 51.41.

**Nhận xét:** mỗi request "slow" bị cộng thêm 1500 ms. p50 giữ nguyên khoảng 190–194 ms ở cả 3 mức, vì phần lớn request không bị ảnh hưởng.
- `0.03`: chỉ p99 nhảy từ 225.6 lên **1709.4 ms** (p99 nằm *trong* nhóm 3 % chậm nhất). p95 vẫn bình thường (253.6 ms), còn avg chỉ tăng 50.7 ms (190.2 → 240.9), khớp ước tính 0.03 × 1500 ≈ 45 ms.
- `0.1` (> 5 %): cả p95 cũng rơi vào nhóm chậm (**1693.8 ms**, gấp 7.7 lần so với 220.2 ms), trong khi avg chỉ tăng 124.8 ms (190.2 → 315.0).
- Nhìn avg = 315 ms thì tưởng hệ thống vẫn ổn (dưới SLO 500 ms), nhưng thực tế cứ khoảng 1 trên 10 người dùng phải chờ ~1.7 s. Average trộn 90 % request nhanh với 10 % request rất chậm thành một con số "trông ổn", vì vậy SLO đặt theo p95/p99. Tail latency còn kéo RPS xuống (101.44 → 51.41) vì các slot concurrency bị giữ lâu hơn.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | 100.0% (23/23) |
| Avg tokens / câu | 56.7 | 109.1 | 116.9 |
| Tổng cost (USD) | 0.000399 | 0.000762 | 0.000818 |
| Cost / câu đúng (USD) | 0.00039915 | 0.00003627 | 0.00003557 |
| Latency p50 / p95 (ms) | 164.4 / 190.4 | 198.0 / 221.3 | 192.3 / 217.5 |
| Regression so với baseline | 0 | 0 | 0 |
| Quality gate | FAIL | PASS | PASS |

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json` (baseline `eval_v1.json`), `reports/eval_v2_fixed.json` (`--min-accuracy 1.0 --baseline eval_v2.json`). Eval chạy với `MOCK_ERROR_RATE=0` (0 lỗi ở cả 3 lần) để chỉ đo chất lượng. `eval_v2.json` được đo trên KB gốc lấy từ commit đề `83e677f`. Câu v2 (KB gốc) trả lời sai đúng 2 câu: `security-02` (thiếu `token, hết hạn, chữ ký`) và `scaling-03` (thiếu `cpu, memory, custom metrics`).

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Keyword bị thiếu | Đã sửa gì |
|---|---|---|
| `security-02` (JWT khác gì API key) | `token`, `hết hạn`, `chữ ký` | KB không có tài liệu về JWT → retrieval rỗng → agent trả lời chung chung. Thêm entry keywords `["jwt", "api key", "xác thực"]` (khớp cả 3 từ trong câu hỏi nên chắc chắn được retrieve): API key là chuỗi bí mật cố định, không tự hết hạn; JWT là **token** tự chứa claims được ký bằng secret, server kiểm tra **chữ ký** và thời điểm **hết hạn** (`exp`) |
| `scaling-03` (Kubernetes HPA scale theo chỉ số nào) | `cpu`, `memory`, `custom metrics` | KB không có tài liệu về HPA. Thêm entry keywords `["hpa", "kubernetes", "autoscal"]`: HPA scale theo **CPU** utilization, **memory** hoặc **custom metrics** (request/giây, độ dài queue qua Prometheus Adapter) |

Chỉ sửa `KNOWLEDGE_BASE` trong `develop/utils/mock_llm.py` (chép y hệt sang `production/utils/mock_llm.py`), không sửa `golden_dataset.jsonl`. Trước khi sửa đã `grep` golden dataset: chỉ 2 câu này chứa `jwt / api key / xác thực / kubernetes / hpa / autoscal`, nên tài liệu được retrieve cho 21 câu còn lại không đổi. Kết quả `eval_v2_fixed.json`: **23/23, 0 regression**, 2 câu được cải thiện.

**Trade-off chất lượng / chi phí / latency giữa v1 và v2:** v2 (đã sửa) thêm bước retrieval và nhét tài liệu vào prompt, nên avg tokens tăng từ 56.7 lên 116.9 (×2.06) và tổng cost tăng từ 0.000399 lên 0.000818 USD (×2.05). Latency tăng nhẹ: p50 từ 164.4 lên 192.3 ms (+27.9 ms), p95 từ 190.4 lên 217.5 ms (+27.1 ms), vẫn xa SLO 500 ms. Đổi lại accuracy tăng từ 4.3 % lên 100 %. Tính theo **cost / câu đúng** thì v2 chỉ tốn 0.00003557 USD, so với 0.00039915 USD của v1, **rẻ hơn khoảng 11.2 lần**, vì v1 trả tiền cho 22 câu trả lời vô dụng. Nếu so bằng "cost / câu" thì ta sẽ chọn nhầm v1 (rẻ hơn ~2 lần mỗi câu). Bản sửa KB làm tăng thêm 7.8 token/câu nhưng giảm cost / câu đúng thêm 1.9 % (0.00003627 → 0.00003557), vì 2 câu sai trước đó giờ cũng có giá trị.

**Quality gate trong CI:** `run_eval.py` thoát với exit code 1 khi accuracy < `--min-accuracy` hoặc có câu regression so với `--baseline`. Workflow [`.github/workflows/eval-quality-gate.yml`](.github/workflows/eval-quality-gate.yml) chạy agent v2 rồi `python eval/run_eval.py --min-accuracy 1.0 --baseline reports/eval_v2_fixed.json` trên mỗi PR; exit code ≠ 0 làm job đỏ, và khi bật *Require status checks to pass* cho nhánh `main` thì PR không merge được. Kiểm chứng: chạy eval v1 với `--baseline reports/eval_v2_fixed.json` → exit code **1** (`Câu bị regression: 22`, accuracy 4.3 % so với baseline 100 %). Ngược lại, eval v2 đã sửa với `--min-accuracy 1.0 --baseline reports/eval_v2.json` trả exit code **0**. Workflow đã chạy thật trên GitHub Actions: [run 45861700684](https://github.com/ThuHang2604/K5-L3B-DAY14-DoThiThuHang-2A202603840-Deployment-Benchmark-Observability/actions/runs/45861700684) trên commit `063b8a8` → `success` (lúc đó chưa có `reports/eval_v2_fixed.json` nên gate chỉ kiểm accuracy).

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build --scale agent=3` trong `06-lab-complete/benchmark-observability/production/`, chạy ngày 2026-11-26 (giờ UTC, GMT+7 = UTC+7). Tải nền: Locust headless 30 user (`run -d --no-deps ... -u 30 -r 5`) bắt đầu 17:04:41Z, chạy suốt các thí nghiệm.

Hai vấn đề gặp khi chạy stack (đã xử lý):
- Image agent của đề crash-loop `ModuleNotFoundError: No module named 'uvicorn'`: user `agent` có HOME=`/app` (`useradd -d /app`) nên Python tìm user site-packages ở `/app/.local`, trong khi package nằm ở `/home/agent/.local`. Sửa bằng `ENV PYTHONUSERBASE=/home/agent/.local` trong `production/Dockerfile`.
- `docker compose run locust ...` (lệnh trong CHECKPOINTS) tự khởi động lại dependency `agent` theo `deploy.replicas: 2`, nên lần đo "1 instance" thực ra chạy 2 instance (Prometheus thấy `count(up{job="agent"})` = 2 từ 16:55:09Z). Lần đo được nộp dùng `run --rm --no-deps`, và Prometheus xác nhận số instance = 1 trong suốt 17:00:03–17:02:04Z, = 3 trong suốt 17:02:31–17:04:32Z.

### 4.1 Screenshot

- [x] [Dashboard Grafana khi có tải](screenshots/grafana-dashboard.png)
- [x] [Trace chậm trong Jaeger](screenshots/jaeger-slow-trace.png)
- [x] [Trace lỗi trong Jaeger](screenshots/jaeger-error-trace.png)
- [x] [Trang Alerts của Prometheus, ≥ 2 alert firing](screenshots/prometheus-alert-firing.png)

**Đọc dashboard (Exercise 7.4):** lấy bằng Prometheus API (`/api/v1/query`, cùng biểu thức với panel) lúc 17:05:43Z, 3 instance, 30 user:
p95 `/ask` = **297.2 ms** (p50 177.3 ms, p99 1875.6 ms, avg 249.2 ms) · error rate = **1.31 %** · tổng 18.09 req/s cho `/ask`; req/s mỗi instance (mọi route) = **6.75 / 6.73 / 6.76** (172.19.0.7 / .8 / .9, Nginx chia đều) · in-flight 1 mỗi instance · tokens 1403 input/s và 744 output/s.
Ảnh Grafana (Last 15 minutes, 00:10 GMT+7) hiển thị Traffic 18.5 req/s, Error rate 1.18 %, Latency p95 293.04 ms, Instances up 3. Đo lại lúc 17:12:18Z: p95 294.5 ms, error 1.54 %, 17.76 req/s, cost $2.43/giờ.

- **Khoảng cách avg ↔ p95/p99:** avg 249.2 ms, p95 297.2 ms, nhưng p99 lên 1875.6 ms. ~3 % request bị cộng 1,5 s kéo đuôi phân phối; avg bị pha loãng bởi 97 % request nhanh nên thấp hơn p99 tới 7,5 lần. Khoảng cách này chính là tail latency.
- **Input token > output token** (1403 so với 744 token/s, gấp 1,9 lần): prompt gửi cho LLM gồm system prompt + tài liệu retrieve được + câu hỏi, còn câu trả lời chỉ là một đoạn ngắn → token input luôn lớn hơn output (đặc trưng của RAG).
- **Vì sao `/metrics` bị chặn ở Nginx (404 từ ngoài):** metrics lộ thông tin nội bộ (số request, cost, version, tên model) và gọi nhiều thì tốn tài nguyên; chỉ Prometheus trong mạng nội bộ Docker được scrape trực tiếp từng instance (Prometheus cần số liệu *theo từng instance*, đi qua LB sẽ ra số của một instance ngẫu nhiên).

### 4.2 Thí nghiệm "phá hệ thống"

Giờ thay đổi = lúc `docker compose up -d --no-deps --scale agent=3 agent` (recreate agent với `.env` mới) hoặc `docker pause` chạy xong. `pending` lấy từ `activeAt` của `/api/v1/alerts`; `firing` là lần đầu poll (mỗi 5 s) thấy state = firing. Sau mỗi thí nghiệm `.env` được trả về `.env.example`.

| # | Thay đổi (giờ UTC) | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2`, đổi lúc 17:12:43Z (cùng lúc với B) | `AgentHighErrorRate` (`for: 1m`) | pending 17:13:07Z (+24 s) → **firing 17:14:08Z (+85 s)**; value 19.2 % | Error rate (1.54 % → 20.6 %) |
| B | `MOCK_SLOW_RATE=0.3`, đổi lúc 17:12:43Z | `AgentHighLatencyP95` (`for: 2m`) | pending 17:13:02Z (+19 s) → **firing 17:15:03Z (+140 s)**; p95 = 1.90 s | Latency p50/p95/p99 (p95 294 ms → 1902 ms, p50 chỉ 207 ms) |
| C | `LLM_MODEL=mock-llm-large`, đổi lúc 17:18:42Z | `LLMCostBurnRateHigh` (`for: 2m`) | pending 17:19:02Z (+20 s) → **firing 17:21:03Z (+141 s)**; value $16.28/giờ | Cost burn rate (USD/giờ): $1.69 → $16.39/giờ; p95 0.29 → 0.67 s |
| D | `docker pause k5-day14-observability-agent-2` (172.19.0.8) lúc 17:21:35Z, unpause 17:22:33Z | `AgentInstanceDown` (`for: 30s`) | pending 17:21:47Z (+12 s) → **firing 17:22:20Z (+45 s)**; hết firing 17:22:40Z (7 s sau unpause) | Instances up (3 → 2); Requests/s theo instance (.8 tụt từ ~6,7 xuống 1,1) |
| E | `AGENT_VERSION=v1`, đổi lúc 17:22:58Z | Không có (theo dõi tới 17:26:00Z) | — | Retrieval hit ratio (17.68 hit/s → 0, series biến mất), Tokens/s (input 1413 → 677/s), Phiên bản đang chạy (`agent_info` v2 → v1) |

Ảnh `prometheus-alert-firing.png` chụp lúc A+B đang chạy: nhóm `agent-slo` FIRING (2): `AgentHighErrorRate` và `AgentHighLatencyP95`. Hệ thống hồi phục: trả `.env` lúc 17:17:36Z → cả 2 alert hết lúc 17:18:31Z.

**Lưu ý về thí nghiệm C:** ngay ở cấu hình mặc định (mock-llm-small, 30 user), cost burn rate đo được **$2.43/giờ** (17:12:18Z), đã vượt ngưỡng $2/giờ của rule, nên `LLMCostBurnRateHigh` firing sẵn từ 16:56:17Z (lúc chạy Locust 100 user) và chỉ về inactive khi thí nghiệm A làm 20 % request lỗi (không tốn token, cost tụt còn $1.94/giờ). C được làm ngay lúc alert đang inactive nên đo được thời gian firing, nhưng ngưỡng $2/giờ quá thấp cho mức tải này. Ở production cần đặt ngưỡng theo budget thật (vd `DAILY_BUDGET_USD / 24` × hệ số) hoặc so với baseline, không thì alert sẽ kêu liên tục và bị phớt lờ.

Đọc kết quả: A firing nhanh hơn B vì `for: 1m` so với `for: 2m`. Cả hai đều cần ~20 s để biểu thức vượt ngưỡng (cửa sổ `rate(...[1m])` cần đủ mẫu, scrape 5 s, evaluation 5 s). D nhanh nhất (+45 s) vì `up == 0` đúng ngay ở lần scrape hỏng đầu tiên và chỉ cần `for: 30s`. Với B, p50 gần như không đổi (207 ms) trong khi p95 lên 1.9 s: đúng mô hình tail latency ở mục 2.2.

**Thí nghiệm E không làm alert nào firing — phát hiện bằng cách nào?** Đổi sang v1 vẫn trả 200 nhanh, thậm chí *nhanh hơn* (p95 294 → 198 ms, error 1.66 % → 1.16 %, 18.1 → 18.8 req/s) vì không còn bước retrieval và prompt ngắn hơn, nên mọi metric hạ tầng đều xanh: lỗi *chất lượng* không phải lỗi *hạ tầng* (theo eval ở mục 3, accuracy giảm từ 100 % xuống 4.3 %). Cần (1) chặn từ trước khi deploy bằng eval quality gate trong CI; (2) giám sát chất lượng khi chạy: chạy eval định kỳ (synthetic eval) với golden dataset vào bản đang chạy và alert khi accuracy giảm, alert trên `retrieval hit ratio` / `agent_info{version}` khác với bản mong đợi; (3) thu tín hiệu từ người dùng (thumbs down, tỉ lệ hỏi lại) và LLM-as-judge chấm mẫu ngẫu nhiên.

**Vì sao lỗi 504 ở thí nghiệm D không hiện trên Error rate của agent:** trong lúc pause, log Nginx có **26 response 504** (`upstream timed out (110: Operation timed out) while reading response header from upstream, ... upstream: "http://172.19.0.8:8000/ask"`), nhưng error rate do agent báo lúc 17:22:28Z chỉ là **1.26 %** (mức nền). Lỗi xảy ra ở Nginx (chờ quá `proxy_read_timeout` vì instance bị pause không trả lời), agent chưa bao giờ xử lý xong request đó nên metric của agent không đếm. Metric chỉ phản ánh những gì chính service đó nhìn thấy → cần thêm metric ở tầng LB hoặc synthetic check từ bên ngoài.

### 4.3 Debug bằng trace

Lấy bằng Jaeger API v3 (`/api/v3/traces?query.service_name=observable-agent`, 1000 trace trong 3 phút trước 17:05:32Z), chọn trace dài nhất. Ảnh: `jaeger-slow-trace.png`.

- `trace_id`: `fc481bc7698720edba42924a278930fd` (bắt đầu 17:05:01.701Z, instance 172.19.0.7)
- Tổng thời gian: **1720.05 ms**
- Các span: `POST /ask` 1720.05 ms → `agent.run` 1717.41 ms (99.8 %) → `agent.retrieve_context` 22.71 ms (1.3 %) và **`chat mock-llm-small` 1694.37 ms (98.5 %)** (76 input / 39 output tokens, $3.48e-05)
- Span chiếm nhiều thời gian nhất: `chat mock-llm-small` (**1694.37 ms, 98.5 %**). Đây là một request bị mock cộng 1,5 s "slow"; retrieval chỉ chiếm 1.3 % nên tối ưu retrieval không giúp gì, nút thắt là LLM upstream.
- Dòng log tương ứng (`docker compose -p k5-day14-observability logs agent | grep fc481bc7698720edba42924a278930fd`):

```json
{"ts": "2026-11-26T17:05:03.420856+00:00", "level": "INFO", "service": "observable-agent", "instance": "464a7d1bf177", "event": "llm_call", "trace_id": "fc481bc7698720edba42924a278930fd", "span_id": "439c4b807ea8de06", "request_id": "91b19f0f93b5", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 76, "output_tokens": 39, "cost_usd": 3.5e-05, "llm_ms": 1693.5}
{"ts": "2026-11-26T17:05:03.421244+00:00", "level": "INFO", "service": "observable-agent", "instance": "464a7d1bf177", "event": "http_request", "trace_id": "fc481bc7698720edba42924a278930fd", "span_id": "439c4b807ea8de06", "request_id": "91b19f0f93b5", "method": "POST", "route": "/ask", "status": 200, "ms": 1719.4}
```

`llm_ms: 1693.5` trong log khớp với span `chat mock-llm-small` 1694.37 ms, và `ms: 1719.4` khớp với tổng 1720.05 ms của trace.

Trace lỗi: span `chat mock-llm-small` có status ERROR, `error.type=upstream_unavailable` và event `exception` "Upstream LLM error: 503 Service Unavailable (mock)". `trace_id` là `18b3dda80a2801d949985c455576f34c` (17:05:27.013Z, tổng 123.53 ms, `POST /ask` trả 503; `chat` fail sau 79.50 ms, sớm hơn request thành công vì mock trả lỗi sau nửa latency). Ảnh: `jaeger-error-trace.png`. Log cùng `trace_id`:

```json
{"ts": "2026-11-26T17:05:27.113461+00:00", "level": "INFO", "service": "observable-agent", "instance": "464a7d1bf177", "event": "llm_error", "trace_id": "18b3dda80a2801d949985c455576f34c", "span_id": "0987f903c62c3443", "request_id": "ec352a5fc19e", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
{"ts": "2026-11-26T17:05:27.113919+00:00", "level": "INFO", "service": "observable-agent", "instance": "464a7d1bf177", "event": "http_request", "trace_id": "18b3dda80a2801d949985c455576f34c", "span_id": "0987f903c62c3443", "request_id": "ec352a5fc19e", "method": "POST", "route": "/ask", "status": 503, "ms": 99.9}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 59.94 | 190 | 270 | 1700 | 78/7168 (1.09%) |
| 3 | 60.41 | 190 | 220 | 1700 | 95/7223 (1.32%) |

(Số lấy từ dòng `/ask` của `benchmark-results/scale_{1,3}_stats.csv`. Lệnh: `docker compose -p k5-day14-observability run --rm --no-deps locust ... --headless -u 100 -r 10 -t 2m`, scale_1 chạy 17:00:03–17:02:04Z, scale_3 chạy 17:02:31–17:04:32Z. Mọi failure đều là `503` do mock giả lập lỗi upstream. Avg `/ask`: 238.6 ms so với 233.1 ms.)

**Giải thích kết quả:** scale từ 1 lên 3 instance gần như **không tăng RPS** (59.94 → 60.41 req/s, +0.8 %), p50 giữ nguyên 190 ms, p99 giữ nguyên 1700 ms. Chỉ p95 giảm từ 270 xuống 220 ms (−50 ms).
- RPS bị giới hạn bởi **tải sinh ra**, không phải bởi server: 100 user Locust nghỉ trung bình 1,25 s (0,5–2 s) giữa các lần gọi, nên tải tối đa ≈ 100 / (1,25 s + ~0,22 s) ≈ 68 req/s cho mọi route. Đo được 65.80 (1 instance) và 65.89 (3 instance) req/s tổng, sát mức trần này. Một instance (giới hạn 0,5 CPU) đã xử lý kịp, nên thêm instance không có tải để nhận thêm.
- p95 giảm 50 ms vì với 1 instance, khoảng 60 request/s dồn vào một event loop (0,5 CPU) nên một số request phải xếp hàng ngắn; chia cho 3 instance thì hàng đợi biến mất. p99 = 1700 ms không đổi vì đó là 3 % request bị mock cộng 1,5 s, chính là thời gian chờ LLM. Trace ở mục 4.3 cho thấy span `chat` chiếm 98.5 % thời gian, nên thêm instance không làm LLM trả lời nhanh hơn.
- Nút thắt là **LLM upstream** và **tải sinh ra**, không phải số instance. Scale ngang chỉ có ích khi instance bão hoà: ở `load_test.py` mục 2.1, một process ở c=100 có p95 2,36 s, đó là lúc nên thêm instance. Muốn đo hiệu quả scale bằng Locust thì phải tăng số user (hoặc giảm `wait_time`) cho tới khi 1 instance bão hoà.

---

## 6. Kết luận

- 3 điều học được:
  1. Average che giấu tail latency; SLO và alert phải đặt theo percentile, và đo bằng load test chứ không bằng vài request lẻ.
  2. "Nhanh" và "đúng" là hai loại benchmark khác nhau: dashboard xanh không có nghĩa câu trả lời đúng → eval với golden dataset phải là quality gate trong CI, so sánh bằng cost / câu đúng.
  3. Metrics → traces → logs nối với nhau bằng `trace_id`: metrics cho biết *có* vấn đề, trace cho biết *ở bước nào*, log cho biết *chi tiết*. Mỗi lớp chỉ thấy những gì chính nó xử lý (504 ở Nginx không có trong metric agent).
- Nếu đưa lên production thật, sẽ thêm: alert theo **error budget burn rate** (nhiều cửa sổ 5m/1h) thay cho ngưỡng cố định; metric và alert ở tầng LB + synthetic check từ bên ngoài; eval định kỳ trên bản đang chạy và alert khi accuracy giảm; alert khi cost/ngày tiến gần `DAILY_BUDGET_USD` (trước khi cost guard trả 402); metric rate limit (số 429 theo thời gian) và trace sampling theo tỉ lệ + giữ lại 100 % trace lỗi/chậm.
