FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir -p /data/models && chown 10001:10001 /data/models
USER 10001
ENV REDIMIND_EMBEDDING_CACHE=/data/models
EXPOSE 8000
CMD ["redimind-server"]
