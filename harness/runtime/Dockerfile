ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN pip install --no-cache-dir PyYAML==6.0.2 jsonschema==4.23.0 psutil==6.1.1
COPY src /opt/ash/src
COPY config /opt/ash/config
COPY protocol /opt/protocol
ENV PYTHONPATH=/opt/ash/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /tmp
USER 65534:65534
ENTRYPOINT ["python", "-m", "agent_safety_harness.engine_worker"]
