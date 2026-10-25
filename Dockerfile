FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# fonts-noto-cjk 是图片卡片的中文字体；ca-certificates 保证 HTTPS
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt ./
RUN pip install -i "$PIP_INDEX_URL" -r requirements.txt

COPY . .

RUN mkdir -p /app/config /app/data /app/logs \
    && cp /app/data/aliases.json /app/default-aliases.json \
    && chmod +x /app/deploy/docker-entrypoint.sh

VOLUME ["/app/config", "/app/data", "/app/logs"]

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
