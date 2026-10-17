FROM python:3.11-slim

WORKDIR /app

# 国内加速（可按需替换/删除）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple || true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY debate ./debate
COPY api ./api
COPY web ./web

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
