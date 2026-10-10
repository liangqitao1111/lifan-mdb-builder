# 理反 Web — Docker 镜像（Linux 服务器部署）
FROM python:3.11-slim

# mdbtools：Linux 读取 .mdb（ACE 仅在 Windows 可用，mdbtools 只读）
RUN apt-get update && apt-get install -y --no-install-recommends mdbtools unixodbc \ 
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
# pywin32 为 Windows 专属（ADOX 建库），Linux 跳过
RUN grep -v "pywin32" backend/requirements.txt > backend/requirements-linux.txt \
    && pip install --no-cache-dir -r backend/requirements-linux.txt

COPY backend backend
COPY index.html index.html
COPY 参数 参数
COPY 地层标注 地层标注

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
WORKDIR /app/backend
# Render 要求监听 $PORT（默认 10000）；本地未设 PORT 时仍用 8000
CMD ["sh", "-c", "python -m uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
