# Deployment Information

> Agent Part 6 (`06-lab-complete/`) deploy lên Railway ngày 2026-11-26 (region `sin1`, Singapore).

## Public URL

https://agent-production-2a341514.up.railway.app

## Platform

**Railway** (project `day14-agent`, environment `production`, service `agent` + database `Redis`) — build bằng `06-lab-complete/Dockerfile`, cấu hình trong `06-lab-complete/railway.toml` / `06-lab-complete/render.yaml`.

## Cách deploy

### Railway (CLI)

```bash
cd 06-lab-complete
railway login
railway init                                   # tạo project mới
railway add --database redis                   # thêm Redis vào project
railway up                                     # build Dockerfile + deploy service agent
# Đặt biến cho service agent (Dashboard → service → Variables, hoặc CLI):
railway variables --set "AGENT_API_KEY=<key-ngẫu-nhiên>" \
                  --set "ENVIRONMENT=production" \
                  --set 'REDIS_URL=${{Redis.REDIS_URL}}'
railway domain                                 # tạo public URL
```

Tạo key: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Không ghi key vào file nào trong repo.
Nếu deploy từ GitHub thay vì CLI: Settings → **Root Directory** = `06-lab-complete`.

### Render (Blueprint)

Dashboard → New → **Blueprint** → chọn repo → Blueprint file path: `06-lab-complete/render.yaml` → Apply. Render tạo web service `day14-production-agent` + Key Value `day14-agent-redis`, tự sinh `AGENT_API_KEY` (xem trong tab Environment của service) và tự nối `REDIS_URL`.

## Environment Variables Set

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `PORT` | do platform cấp | Dockerfile CMD nghe `${PORT:-8000}` |
| `ENVIRONMENT` | `production` | thiếu `AGENT_API_KEY` thì app dừng ngay khi start |
| `AGENT_API_KEY` | (secret, đặt trên platform) | không có trong code / git |
| `REDIS_URL` | tham chiếu Redis của platform | `${{Redis.REDIS_URL}}` (Railway) / `fromService` (Render) |
| `RATE_LIMIT_PER_MINUTE` | `10` (mặc định) | |
| `DAILY_BUDGET_USD` / `MONTHLY_BUDGET_USD` | `1.0` / `10.0` (mặc định) | |
| `LOG_LEVEL` | `INFO` | |

## Test Commands (4 lệnh tự kiểm CP6)

```bash
BASE=https://agent-production-2a341514.up.railway.app
AGENT_API_KEY=$(cat ~/day14_agent_key.txt)   # key nằm ngoài repo, không dán vào file này

curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả (2026-11-26T16:36:42Z):

```text
200
200
401
200
```

Body tương ứng:

```text
GET  /health → {"status":"ok","environment":"production","uptime_seconds":24.0}
GET  /ready  → {"ready":true}
POST /ask (không key) → {"detail":"Invalid or missing API key. Send header: X-API-Key: <key>"}
POST /ask (có key)    → HTTP/2 200
  x-request-id: a611bf625d45
  x-railway-edge: sin1
  {"question":"What is Docker?","answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!","user_id":null,"usage":{"input_tokens":3,"output_tokens":18,"cost_usd":1.125e-05},"rate_limit":{"limit":10,"remaining":8},"request_id":"a611bf625d45"}
```

Rate limit (thêm):

```bash
for i in $(seq 1 12); do
  curl -sS -o /dev/null -w '%{http_code} ' -X POST "$BASE/ask" -H 'Content-Type: application/json' \
       -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"test"}'
done; echo   # 10 lần 200 rồi 429
```

Kết quả (2026-11-26T16:39:35Z, sau khi chờ 62 giây cho window cũ hết hạn):

```text
200 200 200 200 200 200 200 200 200 200 429 429
HTTP/2 429
retry-after: 54
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":54}}
```

Service chạy 2 worker uvicorn (`WEB_CONCURRENCY` mặc định 2) nhưng vẫn chặn đúng ở request thứ 11, vì bộ đếm sliding window nằm trong Redis chứ không nằm trong RAM của từng worker.

Kiểm tra bằng script chấm:

```bash
cd 06-lab-complete && python check_production_ready.py --static-only --public-url "$BASE"
```

```text
     ✅ Public URL: GET https://agent-production-2a341514.up.railway.app/health → 200
  Result: 3/12 tiêu chí đạt · 29/29 checks passed
```

(`--static-only` không chạy phần runtime nên luôn ra 3/12; bản đầy đủ chạy local ra PASS 12/12, xem `MISSION_ANSWERS.md` Part 6.)

Log khởi động trên Railway (`railway logs -s agent`):

```text
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)
INFO:     Started parent process [458]
INFO:     Started server process [467]
INFO:     Started server process [466]
INFO:     Application startup complete.
[INFO]  ts="2026-11-26T16:36:19.227333+00:00" event="startup" environment="production" port=8080 rate_limit_per_minute=10 daily_budget_usd=1 monthly_budget_usd=10 redis_ok=true
[INFO]  ts="2026-11-26T16:36:39.326890+00:00" event="request" request_id="17ebb8ae2fe0" method="GET" path="/health" status=200 ms=16.9
[INFO]  ts="2026-11-26T16:36:40.899934+00:00" event="request" request_id="e8ad3bcaec58" method="GET" path="/ready" status=200 ms=6.7
[INFO]  ts="2026-11-26T16:36:41.786595+00:00" event="request" request_id="a4cbfb7572b4" method="POST" path="/ask" status=401 ms=5.3
```

Railway cấp `PORT=8080`; app nghe đúng port đó và `redis_ok=true` lúc khởi động.

## Ghi chú khi deploy

- `railway up` chạy trực tiếp trong `06-lab-complete/` (qua `/mnt/c` của WSL) thì build lỗi `header key "exclude-patterns" contains value with non-printable ASCII characters`. `.dockerignore` không phải nguyên nhân: đã thử deploy với chính `.dockerignore` hiện tại (có comment tiếng Việt) từ một bản copy sạch và build thành công. Cách đã dùng: copy các file git đang theo dõi trong `06-lab-complete/` (trừ `benchmark-observability/`, thư mục này vốn đã nằm trong `.dockerignore`) sang `/tmp/day14-deploy`, `railway link` rồi `railway up -s agent --ci`. Code chạy trên Railway giống hệt commit trên `main`.
- Railway CLI 5.75.0 báo `railway.toml` (Config as Code) đã deprecated và chỉ còn được dùng tới 2026-12-01. Sau ngày đó cần chạy `railway config migrate`.

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
