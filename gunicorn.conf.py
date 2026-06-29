import os

bind = "0.0.0.0:8000"

# 2 processes × 4 threads = 8 concurrent requests.
# gthread workers let background Gemini threads live alongside HTTP threads
# in the same process — they won't be killed between requests.
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# Give long Gemini calls room to breathe (120 s). This is per-request timeout,
# not relevant for async background threads, but protects against hung requests.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Log to stdout/stderr so Docker captures everything via `docker logs`.
accesslog = "-"
errorlog  = "-"
loglevel  = os.environ.get("LOG_LEVEL", "info").lower()

# Do NOT preload — each worker must run create_app() independently so that
# _start_job_monitor() spawns its own daemon thread per worker.
preload_app = False
