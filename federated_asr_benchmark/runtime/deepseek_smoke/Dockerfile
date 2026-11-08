FROM node:24.14.1-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global @openai/codex@0.122.0 && npm cache clean --force \
    && useradd --uid 10001 --create-home evaluator
ENV HOME=/tmp/evaluator CODEX_HOME=/work/.codex PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY runtime.py /opt/runtime.py
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["python3", "/opt/runtime.py"]
