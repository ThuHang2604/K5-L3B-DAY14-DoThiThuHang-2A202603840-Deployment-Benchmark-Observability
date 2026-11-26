# Day 14 Lab — Mission Answers

> Repo: `K5-L3B-DAY14-DoThiThuHang-2A202603840-Deployment-Benchmark-Observability`
> Mọi số đo / output trong file này dán từ lệnh chạy thật trên máy (Windows 11 + WSL2 Ubuntu + Docker Desktop), ngày 2026-11-26.

---

## Part 1: Localhost vs Production

### Exercise 1.1 — Anti-patterns trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Dòng code | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret | `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` | Push lên GitHub là lộ key, bot quét repo dùng key trong vài phút |
| 2 | Hardcode credential DB trong connection string | `DATABASE_URL = "postgresql://admin:password123@localhost:5432/mydb"` | Lộ mật khẩu DB; đổi môi trường phải sửa code |
| 3 | Log ra secret | `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | Secret nằm trong log của platform, ai đọc log cũng thấy |
| 4 | `print()` thay vì logging có cấu trúc | `print(f"[DEBUG] Got question: ...")` | Không có level, timestamp, request_id; log aggregator không parse/lọc được |
| 5 | Config cố định trong code | `DEBUG = True`, `MAX_TOKENS = 500` | Không đổi được giữa dev/staging/prod nếu không sửa code và build lại |
| 6 | Không có health check | (không có `/health`, `/ready`) | Platform không biết app treo để restart; LB vẫn gửi traffic vào instance chết |
| 7 | Bind `localhost` | `host="localhost"` | Trong container chỉ nhận kết nối từ chính container → từ ngoài không gọi được |
| 8 | Port cố định | `port=8000` | Railway/Render cấp port qua biến `PORT`, app nghe sai port → health check fail |
| 9 | `reload=True` khi chạy | `uvicorn.run(..., reload=True)` | Tốn tài nguyên theo dõi file, không dùng cho production |
| 10 | Không xử lý shutdown | không có lifespan / SIGTERM | Khi redeploy, request đang chạy bị cắt ngang |
| 11 | Input không validate | `def ask_agent(question: str)` qua query string | Không giới hạn độ dài câu hỏi, không có schema rõ ràng |

### Exercise 1.2 — Chạy bản develop

```bash
cd 01-localhost-vs-production/develop
pip install -r requirements.txt && python app.py
curl -X POST "http://localhost:8000/ask?question=Hello"
```
Output:

```json
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```
Bản production:

```text
GET /health → {"status":"ok","uptime_seconds":31.6,"version":"1.0.0","environment":"development",...}
GET /ready  → {"ready":true}
POST /ask   → {"question":"Hello","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận.","model":"gpt-4o-mini"}
```

Nhận xét: app chạy được và trả lời, nhưng chưa production-ready: secret nằm trong code và bị in ra terminal ở mỗi request (thấy dòng `[DEBUG] Using key: sk-...`), không có `/health`, chỉ nghe `localhost:8000`.

### Exercise 1.3 — So sánh develop vs production

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong `app.py` | `config.py` đọc env var (`PORT`, `HOST`, `DEBUG`, `AGENT_API_KEY`…) | Cùng một image chạy dev/staging/prod chỉ bằng cách đổi env; secret không vào git |
| Secret | Hardcode + in ra log | Đọc env, không bao giờ log | Lộ secret là sự cố bảo mật; log thường được nhiều người/hệ thống đọc |
| Health check | Không có | `/health` (liveness) + `/ready` (readiness) | Platform tự restart khi app chết; LB chỉ route vào instance sẵn sàng |
| Logging | `print()` | JSON qua `logging` (`event`, `question_length`…) | Log aggregator (Loki, Datadog) parse, lọc, đếm được; không log nội dung nhạy cảm |
| Shutdown | Đột ngột | `lifespan` + handler SIGTERM, `is_ready=False` khi tắt | Request đang chạy được hoàn thành, không mất dữ liệu khi redeploy |
| Host / Port | `localhost:8000` cố định | `0.0.0.0` + `PORT` từ env | Chạy được trong container và trên cloud (port do platform cấp) |
| Debug/reload | Luôn bật | Chỉ bật khi `DEBUG=true` | Tránh lộ thông tin debug và tốn tài nguyên trên production |
| CORS | Không cấu hình | `allowed_origins` từ config | Chỉ frontend được phép mới gọi được API từ trình duyệt |
| Validate input | Không | Trả 422 khi thiếu `question` | Lỗi rõ ràng cho client thay vì 500 |

Ghi chú khi đọc code: `signal.signal(SIGTERM, handle_sigterm)` ở bản production được đăng ký lúc import, nhưng khi `uvicorn.run()` chạy, uvicorn cài handler của chính nó đè lên, nên phần graceful shutdown thực sự do uvicorn + `lifespan` làm. Handler tự viết chỉ chạy lại sau khi uvicorn tắt xong (uvicorn khôi phục handler cũ và phát lại tín hiệu).

---

## Part 2: Docker

### Exercise 2.1 — `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Debian đầy đủ có sẵn Python 3.11, compiler và nhiều thư viện hệ thống (nặng, khoảng 1 GB).
2. **Working directory:** `/app`. Mọi lệnh `COPY`/`RUN`/`CMD` sau đó chạy tương đối với thư mục này.
3. **Vì sao COPY `requirements.txt` trước:** Docker cache theo từng layer. Nếu `requirements.txt` không đổi thì layer `pip install` được dùng lại, sửa code chỉ phải build lại layer `COPY app.py`. Nếu `COPY . .` trước thì mỗi lần sửa code đều cài lại toàn bộ dependencies.
4. **CMD vs ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi `docker run image <lệnh khác>`. `ENTRYPOINT` là lệnh cố định của container; tham số của `docker run` được nối vào sau nó (chỉ ghi đè bằng `--entrypoint`). Hay dùng `ENTRYPOINT ["uvicorn"]` + `CMD ["app:app", "--port", "8000"]` để đổi tham số dễ dàng.

### Exercise 2.2 — Build và chạy bản develop

```bash
docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
docker run --rm -p 8000:8000 my-agent:develop
curl -X POST "http://localhost:8000/ask?question=What+is+Docker"
```

Output (chạy 2026-11-26):

```text
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}
```

Log container: `INFO: 172.17.0.1:36690 - "POST /ask?question=What+is+Docker HTTP/1.1" 200 OK`

### Exercise 2.3 — Multi-stage build (`02-docker/production/Dockerfile`)

- **Stage 1 (builder):** `python:3.11-slim` + cài `gcc`, `libpq-dev`, chạy `pip install --user -r requirements.txt` vào `/root/.local`. Stage này chỉ để build, không được deploy.
- **Stage 2 (runtime):** `python:3.11-slim` sạch, tạo user `appuser`, chỉ copy `/root/.local` (packages đã cài) + `main.py` + `utils/`, chạy bằng user non-root, có `HEALTHCHECK`, `CMD uvicorn ... --workers 2`.
- **Vì sao nhỏ hơn:** image cuối không chứa compiler, header, cache apt/pip và các layer trung gian của builder; base `slim` cũng nhỏ hơn base đầy đủ rất nhiều.

```bash
docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
docker images my-agent
```

```text
IMAGE               ID             DISK USAGE   CONTENT SIZE   EXTRA
my-agent:advanced   f84866cc4764        240MB         57.4MB
my-agent:develop    4883d787884f       1.67GB          426MB
```

| Image | Size (đo bằng `docker images`, cột DISK USAGE) |
|---|---|
| `my-agent:develop` | 1667.8 MB (1.67 GB; `docker image inspect` = 1 667 771 656 bytes) |
| `my-agent:advanced` | 239.8 MB (240 MB; 239 811 271 bytes) |
| Chênh lệch | **85.6 %** = (1667.8 − 239.8) / 1667.8 × 100 |

Theo cột CONTENT SIZE (dung lượng nén) thì 426 MB → 57.4 MB, cũng giảm khoảng 86.5 %. Image production nhỏ hơn khoảng 7 lần.

### Exercise 2.4 — Docker Compose stack (`02-docker/production/docker-compose.yml`)

```
                 ┌───────────── network "internal" (bridge) ───────────────┐
Client ──:80──▶  │ nginx ──round-robin──▶ agent:8000 ──▶ redis:6379        │
                 │ (reverse proxy,                   └──▶ qdrant:6333      │
                 │  rate limit 10r/s/IP)                                    │
                 └──────────────────────────────────────────────────────────┘
```

- Service được start: `agent` (build từ Dockerfile, target `runtime`), `redis` (cache/session/rate limit), `qdrant` (vector DB cho RAG), `nginx` (cổng vào duy nhất, publish 80/443).
- Giao tiếp: các service nói chuyện qua DNS nội bộ của Compose bằng tên service (`redis://redis:6379/0`, `http://qdrant:6333`, `proxy_pass` tới `agent:8000`). Chỉ Nginx mở port ra ngoài; agent không publish port. `depends_on` + `condition: service_healthy` đảm bảo agent chỉ start khi Redis và Qdrant healthy.

```bash
cd 02-docker/production && touch .env.local
docker compose -p k5-day14-docker up -d --build
curl http://localhost/health
curl -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
docker compose -p k5-day14-docker down
```

`main.py` bản production đọc `question` từ JSON body (`await request.json()`), không phải query string, nên lệnh mẫu phải gửi JSON.

Output (chạy 2026-11-26):

```text
$ docker compose -p k5-day14-docker ps
agent  Up 8 seconds (healthy)
nginx  Up 8 seconds
qdrant Up 19 seconds (healthy)
redis  Up 19 seconds (healthy)

$ curl http://localhost/health
{"status":"ok","uptime_seconds":7.4,"version":"2.0.0","timestamp":"2026-11-26T16:18:23.420957"}

$ curl -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question": "Explain microservices"}'
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

---

## Part 3: Cloud Deployment

### Exercise 3.1 — Deploy (agent Part 6)

- Nền tảng chính: **Railway** (project `day14-agent`: service `agent` build từ `06-lab-complete/Dockerfile` + database Redis; biến `AGENT_API_KEY`, `ENVIRONMENT=production`, `REDIS_URL=${{Redis.REDIS_URL}}` đặt bằng `railway variable set`)
- URL: https://agent-production-2a341514.up.railway.app — chi tiết, biến môi trường và ảnh: [DEPLOYMENT.md](DEPLOYMENT.md)
- 4 lệnh tự kiểm CP6 (2026-11-26T16:36:42Z): `/health` **200**, `/ready` **200**, `/ask` không key **401**, `/ask` có key **200**. Vòng lặp 12 request có key: 10 × 200 rồi 429 (`retry-after: 54`).
- Ảnh: [dashboard](screenshots/dashboard.png) · [running](screenshots/running.png) · [test](screenshots/test.png)

### Exercise 3.2 — So sánh `railway.toml` và `render.yaml` (trong `06-lab-complete/`)

| Điểm | `railway.toml` | `render.yaml` |
|---|---|---|
| Phạm vi | Cấu hình **một service** (build + deploy) | **Blueprint** cho cả hạ tầng: nhiều service (web + Redis/Key Value) trong 1 file |
| Định dạng | TOML, bảng `[build]`, `[deploy]` | YAML, danh sách `services:` |
| Build | `builder = "DOCKERFILE"` | `runtime: docker`, `dockerfilePath`, `dockerContext` |
| Health check | `healthcheckPath = "/health"`, `healthcheckTimeout` | `healthCheckPath: /health` |
| Restart | `restartPolicyType = "ON_FAILURE"`, số lần retry | Render tự restart, không cấu hình trong file |
| Biến môi trường | **Không** khai báo trong file; đặt bằng `railway variables set` / Dashboard; Redis tham chiếu `${{Redis.REDIS_URL}}` | Khai báo ngay trong `envVars`; secret dùng `generateValue: true` hoặc `sync: false` (điền trên Dashboard); `REDIS_URL` lấy bằng `fromService` từ service Key Value |
| Port | Railway cấp `PORT`, CMD đọc `${PORT:-8000}` | Render cấp `PORT` (mặc định 10000), cùng CMD |
| Region/plan | Chọn trên Dashboard | `region: singapore`, `plan: free` ngay trong file |

Vì sao khác: Railway coi config-as-code là phần bổ sung cho Dashboard (hạ tầng như Redis thêm bằng plugin), còn Render Blueprint là Infrastructure-as-Code đầy đủ: commit file là tạo/đồng bộ được cả web service lẫn Redis.

### Exercise 3.3 — Pipeline Cloud Run (`03-cloud-deployment/production-cloud-run/`)

`cloudbuild.yaml` chạy 4 bước nối tiếp nhau bằng `waitFor`: (1) **test** — cài requirements + `pytest`, fail là dừng; (2) **build** — `docker build` gắn 2 tag `:$COMMIT_SHA` và `:latest`, dùng `--cache-from` image cũ để build nhanh; (3) **push** — đẩy cả 2 tag lên Container Registry; (4) **deploy** — `gcloud run deploy` image đúng commit lên region `asia-southeast1`, 512Mi/1 CPU, `min-instances=1` để tránh cold start, `max-instances=10`, secret lấy từ Secret Manager. `service.yaml` mô tả trạng thái mong muốn của service (Knative): autoscale 1–10 instance, mỗi instance tối đa 80 request đồng thời, liveness probe `/health`, startup probe `/ready`, secret qua `secretKeyRef`. Deploy theo tag commit nên rollback chỉ cần deploy lại SHA cũ.

---

## Part 4: API Security

### Exercise 4.1 — API key (`04-api-gateway/develop/app.py`)

- **Check ở đâu:** dependency `verify_api_key()` đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)`, gắn vào `/ask` bằng `Depends(verify_api_key)`. `/` và `/health` public.
- **Sai key:** thiếu key → **401** "Missing API key"; có key nhưng sai → **403** "Invalid API key". (Ở Part 6 mình trả **401 cho cả hai** theo hợp đồng của script chấm.)
- **Rotate key:** key đọc từ env `AGENT_API_KEY` → đổi biến trên platform rồi restart/redeploy. Để không downtime nên cho app chấp nhận đồng thời key cũ và key mới trong một khoảng thời gian (vd `AGENT_API_KEYS=new,old`), cập nhật client, rồi bỏ key cũ.
- Lưu ý: bản develop có default `"demo-key-change-in-production"` và in key ra console khi start — không nên làm ở production.

Chạy `AGENT_API_KEY=secret-key-123 python app.py` trong `04-api-gateway/develop` (2026-11-26). Bản develop đọc `question` từ JSON body nên gọi bằng `-d '{"question": ...}'` (query string `?question=` làm `request.json()` lỗi → 500).

```text
$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -d '{"question": "What is an API gateway?"}'
HTTP/1.1 401 Unauthorized
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -H "X-API-Key: wrong-key" -d '{"question": "What is an API gateway?"}'
HTTP/1.1 403 Forbidden
content-type: application/json

{"detail":"Invalid API key."}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -H "X-API-Key: secret-key-123" -d '{"question": "What is an API gateway?"}'
HTTP/1.1 200 OK
content-type: application/json

{"question":"What is an API gateway?","answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}
```

### Exercise 4.2 — JWT (`04-api-gateway/production/auth.py`)

Flow: `POST /auth/token` với `student`/`demo123` → `authenticate_user` kiểm tra → `create_token` ký payload `{sub, role, iat, exp(+60 phút)}` bằng HS256 + `JWT_SECRET` → client gửi `Authorization: Bearer <token>` → `verify_token` kiểm chữ ký và hạn → trả `{username, role}` cho endpoint. Server không cần tra DB mỗi request (stateless). Token hết hạn → 401, chữ ký sai → 403.

```text
$ curl -s -X POST localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
{"access_token":"eyJ...***","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer eyJ...***"}

$ curl -i -X POST localhost:8000/ask -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d '{"question": "what is docker?"}'
HTTP/1.1 200 OK
content-type: application/json
x-content-type-options: nosniff
x-frame-options: DENY
x-xss-protection: 1; mode=block
referrer-policy: strict-origin-when-cross-origin

{"question":"what is docker?","answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!","usage":{"requests_remaining":9,"budget_remaining_usd":1.9e-05}}
```

### Exercise 4.3 — Rate limiting (`rate_limiter.py`)

- **Thuật toán:** sliding window log — mỗi user một `deque` timestamp, bỏ các timestamp cũ hơn 60s, đếm số còn lại.
- **Giới hạn:** user 10 request/phút (`rate_limiter_user`), admin 100 request/phút (`rate_limiter_admin`).
- **Admin "bypass":** `/ask` chọn limiter theo `role` trong JWT — role `admin` dùng limiter 100 req/phút.
- Vượt giới hạn → **429** kèm `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining: 0`. Hạn chế: state nằm trong RAM của process → nhiều instance/worker mỗi cái đếm riêng và mất khi restart (Part 6 chuyển sang Redis).

Vòng lặp 20 request bằng cùng token, chạy ngay sau request 200 ở trên (request đó đã dùng 1/10 lượt):

```text
$ for i in $(seq 1 20); do curl -s -o /dev/null -D /tmp/h -w "%{http_code} " -X POST localhost:8000/ask -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" -d "{\"question\": \"test $i\"}"; grep -iE "^(retry-after|x-ratelimit-remaining):" /tmp/h; done
req  1: 200
req  2: 200
...
req  9: 200
req 10: 429 x-ratelimit-remaining: 0 retry-after: 59
req 11: 429 x-ratelimit-remaining: 0 retry-after: 59
...
req 20: 429 x-ratelimit-remaining: 0 retry-after: 59

$ curl -i -X POST localhost:8000/ask ... -d '{"question": "one more"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795710052
retry-after: 59
content-type: application/json

{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

Tổng cộng đúng 10 request 200 trong 60 giây (1 request ở Exercise 4.2 + 9 request trong vòng lặp), request thứ 11 trở đi bị 429.

### Exercise 4.4 — Cost guard (cách mình làm ở Part 6, `06-lab-complete/app/cost_guard.py`)

- Mỗi request tính `cost = input_tokens/1000 × $0.00015 + output_tokens/1000 × $0.0006` (token ≈ số ký tự / 4).
- Tổng chi phí lưu trong **Redis** với 2 key: `cost:daily:YYYY-MM-DD` (TTL 2 ngày) và `cost:monthly:YYYY-MM` (TTL 32 ngày) → tự "reset đầu tháng" vì sang tháng mới là key mới; mọi instance dùng chung tổng.
- `check_budget()` chạy **trước** khi gọi LLM: đã tiêu ≥ `DAILY_BUDGET_USD` hoặc ≥ `MONTHLY_BUDGET_USD` (mặc định $10/tháng) → **402 Payment Required**.
- `record_cost()` chạy **sau** khi gọi LLM, cộng bằng `INCRBYFLOAT` trong một transaction (atomic, không bị race khi nhiều worker cùng ghi).
- Khác với lời giải mẫu trong CODE_LAB (cộng `estimated_cost` trước khi gọi): mình cộng chi phí **thật** sau khi có số token output. Đánh đổi: request cuối cùng có thể vượt budget một chút (tối đa chi phí của 1 request), đổi lại số liệu chính xác.
- Rate limit bảo vệ **tốc độ** (chống spam/DoS), cost guard bảo vệ **tiền** (kể cả khi người dùng gọi chậm nhưng đều đặn cả ngày) → cần cả hai.

---

## Part 5: Scaling & Reliability

### Exercise 5.1 — Health vs readiness

- `/health` (liveness): trả 200 khi process còn chạy (bản develop kèm uptime, version, % RAM qua psutil). Fail → platform **restart** container.
- `/ready` (readiness): 503 khi đang khởi động/đang tắt (`_is_ready=False`), 200 khi sẵn sàng. Fail → LB **ngừng gửi traffic**, không restart.
- Vì sao `/health` không được kiểm tra Redis: nếu Redis chết mà liveness fail thì platform restart toàn bộ agent liên tục, không giải quyết gì (Redis vẫn chết). Phụ thuộc bên ngoài thuộc về readiness. Part 6 làm đúng như vậy: `/health` luôn 200, `/ready` ping Redis → 503 khi mất kết nối.

### Exercise 5.2 — Graceful shutdown

- Uvicorn bắt SIGTERM: ngừng nhận kết nối mới → chờ request in-flight xong → chạy phần sau `yield` của `lifespan` (`_is_ready=False`, chờ `_in_flight_requests` về 0, tối đa 30s) → thoát.
- `handle_sigterm` đăng ký lúc import chỉ để log; uvicorn cài handler riêng đè lên khi chạy. Nếu tự viết handler mà **không gọi lại** handler của uvicorn thì process không bao giờ tắt (đúng như cảnh báo trong TODO Part 6).
- Part 6: hook SIGTERM bọc handler của uvicorn (đánh dấu draining để `/ready` trả 503, rồi gọi lại handler cũ), Docker `CMD` dùng `exec uvicorn` để uvicorn là PID 1 nhận được SIGTERM từ `docker stop`.

Test (WSL Ubuntu, `05-scaling-reliability/develop`, 2026-11-26). `/ask` gọi mock LLM mất khoảng 3 giây; gửi SIGTERM 0,5 giây sau khi gửi request:

```text
$ python app.py &  PID=$!
$ curl -s localhost:8000/health
{"status":"ok","uptime_seconds":0.8,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T16:20:30.308167+00:00","checks":{"memory":{"status":"ok","used_percent":14.2}}}  [HTTP 200]
$ curl -s localhost:8000/ready
{"ready":true,"in_flight_requests":1}  [HTTP 200]

23:20:30.319 request sent      (curl -X POST "localhost:8000/ask?question=Hello" &)
23:20:30.825 kill -TERM 1316
23:20:33.373 curl done
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}  [HTTP 200, 3.041946s]
server exited
```

Log server:

```text
INFO:     Shutting down
INFO:     127.0.0.1:39324 - "POST /ask?question=Hello HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-26 23:20:33,467 INFO 🔄 Graceful shutdown initiated...
2026-11-26 23:20:33,467 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [1316]
2026-11-26 23:20:33,467 INFO Received signal 15 — uvicorn will handle graceful shutdown
```

Nhận xét: uvicorn in `Shutting down` ngay khi nhận SIGTERM nhưng vẫn chờ request in-flight trả **200** rồi mới chạy phần shutdown của `lifespan`. Dòng `Received signal 15` của `handle_sigterm` xuất hiện **cuối cùng**, sau `Finished server process`, đúng như nhận xét ở Part 1: uvicorn cài handler riêng đè lên, khi tắt xong mới khôi phục handler cũ và phát lại tín hiệu.

### Exercise 5.3 — Stateless

`05-scaling-reliability/production/app.py` lưu session/history ở Redis (`session:{id}`, `setex` TTL 1 giờ) thay vì dict trong RAM. Khi có nhiều instance sau LB, request sau của cùng user có thể rơi vào instance khác; nếu state trong RAM thì instance đó không biết history → mất ngữ cảnh. Với Redis, instance nào cũng đọc được, nên scale ngang hoặc kill instance không mất state. (Bản này vẫn fallback `_memory_store` khi không có Redis — chỉ để demo, production nên fail readiness thay vì âm thầm dùng RAM.)

### Exercise 5.4 — Load balancing

```bash
cd 05-scaling-reliability/production && touch .env.local
docker compose -p k5-day14-scaling up -d --build --scale agent=3
```

Nginx `upstream agent_cluster { server agent:8000; }` — DNS của Compose trả về 3 IP, Nginx round-robin; `proxy_next_upstream error timeout http_503` + `proxy_next_upstream_tries 3` chuyển request sang instance khác khi 1 instance lỗi. Header `X-Served-By` cho thấy instance phục vụ.

Kết quả (2026-11-26):

```text
$ docker compose -p k5-day14-scaling ps
k5-day14-scaling-agent-1 Up 14 seconds (healthy)
k5-day14-scaling-agent-2 Up 14 seconds (healthy)
k5-day14-scaling-agent-3 Up 14 seconds (healthy)
k5-day14-scaling-nginx-1 Up 13 seconds
k5-day14-scaling-redis-1 Up 24 seconds (healthy)

$ for i in $(seq 1 6); do curl -s -D - -o /dev/null http://localhost:8080/health | grep -i x-served-by; done
X-Served-By: 172.19.0.5:8000
X-Served-By: 172.19.0.3:8000
X-Served-By: 172.19.0.4:8000
X-Served-By: 172.19.0.5:8000
X-Served-By: 172.19.0.3:8000
X-Served-By: 172.19.0.4:8000

$ docker compose -p k5-day14-scaling logs agent   (bỏ dòng GET /health)
agent-3  | INFO:app:Starting instance instance-3bcb66
agent-3  | INFO:app:Storage: Redis ✅
agent-1  | INFO:app:Starting instance instance-3a6f1b
agent-1  | INFO:app:Storage: Redis ✅
agent-2  | INFO:app:Starting instance instance-4c96bd
agent-2  | INFO:app:Storage: Redis ✅
agent-1  | INFO:     172.19.0.6:58386 - "POST /chat HTTP/1.1" 200 OK
agent-1  | INFO:     172.19.0.6:58386 - "POST /chat HTTP/1.1" 200 OK
agent-3  | INFO:     172.19.0.6:38334 - "POST /chat HTTP/1.1" 200 OK
agent-3  | INFO:     172.19.0.6:38334 - "POST /chat HTTP/1.1" 200 OK
agent-2  | INFO:     172.19.0.6:46460 - "POST /chat HTTP/1.1" 200 OK
agent-2  | INFO:     172.19.0.6:46460 - "GET /chat/6b9df6a1-1872-4bfc-b778-83ed2f5a5be9/history HTTP/1.1" 200 OK
```

Nginx xoay vòng đều qua 3 IP (`.5 → .3 → .4 → .5 …`), và cả 3 container đều nhận `POST /chat`.

### Exercise 5.5 — Test stateless

```bash
python test_stateless.py
docker compose -p k5-day14-scaling down
```

Output:

```text
Session ID: 6b9df6a1-1872-4bfc-b778-83ed2f5a5be9

Request 1: [instance-3bcb66]
  Q: What is Docker?
Request 2: [instance-3a6f1b]
  Q: Why do we need containers?
Request 3: [instance-4c96bd]
  Q: What is Kubernetes?
Request 4: [instance-3bcb66]
  Q: How does load balancing work?
Request 5: [instance-3a6f1b]
  Q: What is Redis used for?

------------------------------------------------------------
Total requests: 5
Instances used: {'instance-3a6f1b', 'instance-3bcb66', 'instance-4c96bd'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  ...
  [user]: What is Redis used for?...
  [assistant]: Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã đư...

✅ Session history preserved across all instances via Redis!
```

Giải thích: 5 câu trong cùng `session_id` được phục vụ bởi các instance khác nhau (`served_by` đổi) nhưng history cuối cùng vẫn đủ các lượt hỏi–đáp, vì history nằm ở Redis chứ không ở RAM của instance nào.

---

## Part 6: Final Project — `06-lab-complete/`

### Kiến trúc

```
Client ──X-API-Key──▶ POST /ask
   1. verify_api_key   (auth.py)        → 401 thiếu/sai key (hmac.compare_digest)
   2. check_rate_limit (rate_limiter.py) → 429 + Retry-After   ┐
   3. check_budget     (cost_guard.py)   → 402                 ├─ state trong Redis
   4. mock LLM                                                 │  (redis_client.py)
   5. record_cost      (cost_guard.py)                         ┘
GET /health → 200 luôn (liveness) · GET /ready → 503 khi Redis down hoặc đang tắt
```

| Tiêu chí | Cách làm |
|---|---|
| 1. Dockerfile | 2 stage `python:3.11-slim` (builder cài deps vào `/opt/venv`, runtime chỉ copy venv + `app/` + `utils/`), user `app` (uid 1000), `HEALTHCHECK` bằng Python urllib, `CMD sh -c "exec uvicorn ... --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"` |
| 2. Compose | `agent` (build `.`, `env_file: .env` `required: false`, `REDIS_URL=redis://redis:6379/0`, `depends_on: redis: service_healthy`) + `redis:7-alpine` có healthcheck và volume |
| 3. .dockerignore | `.env`, `.env.*` (giữ `.env.example`), `__pycache__/`, `venv/`, `.venv/`, `.git/`, `benchmark-observability/`, docs |
| 4–5. Health/Ready | `/health` không đụng Redis; `/ready` ping Redis (timeout 1s) và trả 503 khi app đang draining |
| 6. API key | `APIKeyHeader("X-API-Key", auto_error=False)` → 401 cho cả thiếu lẫn sai; so sánh constant-time |
| 7. Rate limit | Sliding window bằng Redis sorted set (`ZREMRANGEBYSCORE`→`ZADD`→`ZCARD`→`EXPIRE` trong `MULTI`), key = hash SHA-256 của API key (không lưu key thô); request bị chặn được xoá khỏi window; 429 + `Retry-After` |
| 8. Cost guard | Xem Exercise 4.4 |
| 9. Config | `config.py` đọc mọi biến bằng `os.getenv`; `AGENT_API_KEY` không có default, `ENVIRONMENT=production` mà thiếu key thì dừng ngay lúc start |
| 10. Logging | `JsonFormatter` riêng cho logger `agent` (`propagate=False`), mỗi request 1 dòng `{"ts","lvl","event":"request","request_id","path","status","ms"}`; header `X-Request-ID` |
| 11. Graceful shutdown | uvicorn chờ request in-flight; hook SIGTERM đánh dấu draining rồi gọi lại handler của uvicorn; `lifespan` đóng Redis + log `shutdown`; `--timeout-graceful-shutdown 8` để `docker stop` xong < 10s |
| 12. Deploy config | `railway.toml` (DOCKERFILE + `/health`) và `render.yaml` (web docker + Key Value Redis, `AGENT_API_KEY: generateValue`) |
| Error handling | 422 có thông báo rõ field nào sai; Redis lỗi → 503 + `Retry-After`; lỗi khác → 500 JSON chỉ có `request_id`, không lộ stack trace |

### Output `python check_production_ready.py` (bản đầy đủ, có Docker)

Chạy trong `06-lab-complete/` trên WSL2 Ubuntu (Python 3.12.16, Docker 29.9.3), 2026-11-26, exit code 0. Không phải sửa code sau lần chạy này.

```text
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
```

Một số dòng chi tiết trong cùng lần chạy: `Docker: build image thành công — 25s`, `Docker: image < 500 MB — 273 MB`, `` Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.7s ``.

Các chế độ nhanh: `--static-only` → `Result: 3/12 tiêu chí đạt · 28/28 checks passed` (chạy cả trên Windows và WSL); `--skip-docker` (WSL) → `Result: 10/12 tiêu chí đạt · 39/39 checks passed` (2 tiêu chí cần Docker để ở ⚠️).

---

## AI log

| Lần | Công cụ | Hỏi gì | Nhận được gì | Mình tự sửa / tự kiểm tra thế nào |
|---|---|---|---|---|
| 1 | Claude (phiên trước) | Đọc đề Day 14 + script chấm, hoàn thành các TODO Part 6 | Code `app/*.py`, Dockerfile multi-stage, compose, `.dockerignore`, `.env.example`, `railway.toml`, `render.yaml` | Claude Code chạy `check_production_ready.py` bản đầy đủ trong WSL2 → **PASS 12/12 · 43/43** ngay lần đầu, không phải sửa code; `--static-only` 28/28 (Windows + WSL), `--skip-docker` 39/39. Code chạy trên Railway giống hệt commit trên `main` |
| 2 | Claude (phiên trước) | Script chạy toàn bộ CP7 và in bảng cho báo cáo từ file JSON/CSV | `scripts/run_part7_local.py`, `scripts/report_tables.py`, workflow `.github/workflows/eval-quality-gate.yml` | Đã chạy cả hai script; mọi bảng trong `BENCHMARK_REPORT.md` dán từ output `report_tables.py`. Workflow chạy thật trên GitHub Actions: run trên `063b8a8` và `70f3e48` đều `success` |
| 3 | Claude (phiên trước) | Gợi ý câu trả lời Part 1–5 dựa trên code mẫu | Nội dung lý thuyết của file này | Mọi output/số đo trong file do Claude Code chạy lệnh thật rồi dán vào (lần 4) |
| 4 | Claude Code (Opus 5.5), 2026-11-26/27 | Chạy toàn bộ phần thực hành Part 2–7, điền số thật, commit theo từng bước | Xem chi tiết bên dưới | Mình đọc lại output, ảnh và từng commit; tự đăng nhập Railway và tự chụp 7 ảnh |

**Chi tiết lần 4 (Claude Code):**

- **Môi trường:** Device Guard của máy chặn `.venv\Scripts\python.exe` và cả `_socket.pyd` của Python 3.12 bản uv trên Windows, nên mọi lệnh Python chạy trong WSL2 Ubuntu (`~/.venvs/day14`, Python 3.12.16). Riêng `check_production_ready.py --static-only` chạy thêm bằng Python 3.15 trên Windows (chỉ cần stdlib). `railway.exe` bản Windows cũng bị chặn, nên cài Railway CLI 5.75.0 bản Linux trong WSL.
- **Lệnh đã chạy:** `docker build`/`run`/`images` (Part 2); `docker compose -p k5-day14-docker|k5-day14-scaling|k5-day14-observability up/down`; `curl -i` cho 401/403/200/429 (Part 4); test SIGTERM bằng `kill -TERM` trong WSL; `test_stateless.py`; `check_production_ready.py` (3 chế độ + `--public-url`); `railway init/add/variable set/up/domain/logs`; 4 lệnh tự kiểm CP6 + vòng lặp rate limit trên URL Railway; `run_part7_local.py`, `report_tables.py`; Locust headless scale 1/3 và tải nền 30 user; truy vấn Prometheus `/api/v1/query`, `/api/v1/alerts`, Jaeger `/api/v3/traces`; thí nghiệm A+B, C, D, E.
- **File đã sửa:** `MISSION_ANSWERS.md`, `DEPLOYMENT.md`, `BENCHMARK_REPORT.md`; **viết mới** 2 entry KB (JWT, HPA) trong `develop/` và `production/utils/mock_llm.py` (khác với dự kiến ban đầu, bản sửa KB chưa có trong working tree, đã `git diff 83e677f` để kiểm tra); `production/Dockerfile` của Part 7 (`ENV PYTHONUSERBASE`, sửa crash-loop `No module named 'uvicorn'`); `.gitignore` (thêm `*.log`). Không sửa `check_production_ready.py`, `golden_dataset.jsonl` hay code Part 1–5.
- **Đã kiểm tra:** eval v2 KB gốc 21/23 (sai đúng `security-02`, `scaling-03`) → `eval_v2_fixed` **23/23, 0 regression**, exit 0; gate v1 so với `eval_v2_fixed` exit **1**. Số instance trong lúc chạy Locust được xác nhận bằng `count(up{job="agent"})` (phát hiện `docker compose run` làm lần đo "1 instance" đầu tiên chạy 2 instance → đo lại bằng `--no-deps`). Log JSON khớp `trace_id` của trace chậm/lỗi. Key Railway lưu ngoài repo (`%USERPROFILE%\day14_agent_key.txt`), đã quét repo + toàn bộ lịch sử commit không có key; `git log --all -- .env "*.env"` rỗng.
- **Sự cố khác:** `railway up` trực tiếp trong `06-lab-complete/` lỗi `exclude-patterns ... non-printable ASCII`, nên đã deploy từ bản copy sạch các file đã commit (ghi trong `DEPLOYMENT.md`). Hai commit `update`/`update 1` không do Claude Code tạo, đã lỡ đưa 2 file `*.log` (không chứa secret) và CSV Locust chưa hợp lệ lên; đã gỡ log ở commit `chore: untrack agent logs and ignore *.log` và thay CSV bằng lần đo đúng.
