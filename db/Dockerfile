FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /service

# 独立服务只依赖 db/query.py + db/server.py，不复制 bot 代码
COPY db/query.py db/server.py ./
COPY db/web ./web

EXPOSE 8090

ENTRYPOINT ["python", "server.py"]
CMD ["--db", "/data/warframe.db", "--host", "0.0.0.0", "--port", "8090"]
