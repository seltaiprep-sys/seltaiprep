# gunicorn_config.py
"""
Gunicorn config for Bisup Starter Cloud (1 vCPU, 2 GB RAM).
Optimized for Flask + Redis + PostgreSQL + AI/audio workloads.
"""

import multiprocessing
import os

# ═══════════════════════════════════════════════════════════
# BIND
# ═══════════════════════════════════════════════════════════
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:10000")

# ═══════════════════════════════════════════════════════════
# WORKERS
# 1 vCPU, 2 GB RAM → 2 gevent workers सुरक्षित
# (sync worker भए 2, gevent भए 2–3)
# ═══════════════════════════════════════════════════════════
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
# 1 vCPU → 3 workers, तर 2GB RAM मा 2 ठीक
if workers > 2:
    workers = 2

# ═══════════════════════════════════════════════════════════
# WORKER CLASS
# gevent = async I/O (AI calls, DB, TTS) को लागि राम्रो
# ═══════════════════════════════════════════════════════════
worker_class = "gevent"
worker_connections = 500          # 2GB RAM मा 500 ठीक (1000 होइन)

# ═══════════════════════════════════════════════════════════
# TIMEOUTS
# AI calls ले 60–120s लिन सक्छ, audio generation अझ बढी
# ═══════════════════════════════════════════════════════════
timeout = 300                     # 5 मिनेट (AI/audio को लागि)
graceful_timeout = 30
keepalive = 5

# ═══════════════════════════════════════════════════════════
# MEMORY MANAGEMENT
# 2GB RAM मा memory leak रोक्न max_requests जरुरी
# ═══════════════════════════════════════════════════════════
max_requests = 500                # प्रत्येक worker 500 requests पछि restart
max_requests_jitter = 50          # अचानक सबै restart नहोस्

# ═══════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
accesslog = os.environ.get("GUNICORN_ACCESSLOG", "logs/gunicorn_access.log")
errorlog = os.environ.get("GUNICORN_ERRORLOG", "logs/gunicorn_error.log")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ═══════════════════════════════════════════════════════════
# PROCESS NAMING
# ═══════════════════════════════════════════════════════════
proc_name = "ielts_app"

# ═══════════════════════════════════════════════════════════
# PRELOAD
# preload_app = True → memory बचत, तर gevent संग सावधानी
# Gevent worker संग preload_app ले केही issue गर्न सक्छ
# ═══════════════════════════════════════════════════════════
preload_app = False               # gevent संग False राख्नु राम्रो

# ═══════════════════════════════════════════════════════════
# ENVIRONMENT
# ═══════════════════════════════════════════════════════════
raw_env = [
    "FLASK_ENV=production",
    "PYTHONUNBUFFERED=1",         # log तुरुन्तै देखियोस्
]

# ═══════════════════════════════════════════════════════════
# SERVER HOOKS (optional, debugging को लागि)
# ═══════════════════════════════════════════════════════════
def on_starting(server):
    server.log.info(" Gunicorn starting — IELTS app")

def on_exit(server):
    server.log.info(" Gunicorn shutting down")

def worker_int(worker):
    worker.log.info(f" Worker {worker.pid} interrupted")

def worker_abort(worker):
    worker.log.warning(f" Worker {worker.pid} aborted (timeout?)")