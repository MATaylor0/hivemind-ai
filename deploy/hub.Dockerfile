# HiveMind hub image. The org directory is baked in, so the hub enforces exactly the org chart that
# was deployed alongside it. Build from the repo root:
#   docker build -f deploy/hub.Dockerfile --build-arg ORG_DIR=examples/contoso -t hivemind-hub .
FROM python:3.12-slim

ARG ORG_DIR=examples/contoso
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[hub,azure]"

COPY ${ORG_DIR} /org

ENV HIVEMIND_ORG=/org \
    PORT=8080
EXPOSE 8080

# HIVEMIND_ENV selects the environment in hivemind.yaml; HIVEMIND_HUB_KEY must be set (Key Vault reference).
# The container's managed identity needs the Azure AI User role on the Foundry project.
CMD ["sh", "-c", "hivemind --org \"$HIVEMIND_ORG\" hub serve --env \"$HIVEMIND_ENV\" --port \"$PORT\""]
