FROM golang:1.26.9-bookworm AS cli
WORKDIR /src
COPY cli/ ./
RUN CGO_ENABLED=0 go build -o /preloop ./cmd/preloop

FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@2.1.296
COPY --from=cli /preloop /usr/local/bin/preloop
COPY examples/claude-app-verification/rig.py /rig/rig.py
USER node
ENV PRELOOP_DISABLE_TELEMETRY=true
ENTRYPOINT ["python3", "/rig/rig.py"]
