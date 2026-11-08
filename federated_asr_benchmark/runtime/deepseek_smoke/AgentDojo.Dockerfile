FROM python:3.13.2-slim-bookworm
RUN pip install --no-cache-dir agentdojo==0.1.35 && useradd --uid 10001 --create-home evaluator
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY benign.py agentdojo_mcp_server.py banking.json /opt/
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["python", "/opt/benign.py"]
