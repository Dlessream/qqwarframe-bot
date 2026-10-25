ARG PYTHON_IMAGE=python:3.11-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn

# fonts-noto-cjk 是图片卡片的中文字体；ca-certificates 保证 HTTPS
RUN if [ -n "$APT_MIRROR" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources; do \
            if [ -f "$f" ]; then \
                sed -i "s|deb.debian.org|$APT_MIRROR|g; s|security.debian.org|$APT_MIRROR|g" "$f"; \
            fi; \
        done; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-noto-cjk \
        ca-certificates \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
COPY requirements.txt ./
RUN pip install -i "$PIP_INDEX_URL" -r requirements.txt

COPY . .

RUN mkdir -p /app/config /app/data /app/logs \
    && cp /app/data/aliases.json /app/default-aliases.json \
    && chmod +x /app/deploy/docker-entrypoint.sh

VOLUME ["/app/config", "/app/data", "/app/logs"]

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
