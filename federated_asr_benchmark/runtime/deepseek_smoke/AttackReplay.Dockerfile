ARG ENV_IMAGE=agent-safety-agentdojo-benign:v1
FROM ${ENV_IMAGE}
COPY attack_replay.py /opt/attack_replay.py
COPY agentdojo_mcp_server.py /opt/agentdojo_mcp_server.py
COPY score_agentdojo_result.py /opt/tools/score_agentdojo_result.py
COPY product_eval /opt/product_eval
COPY manifests /opt/manifests
ENTRYPOINT ["python", "/opt/attack_replay.py"]
