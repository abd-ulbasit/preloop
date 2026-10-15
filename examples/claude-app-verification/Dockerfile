FROM golang:1.26.9-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c AS cli
WORKDIR /src
COPY cli/ ./
RUN CGO_ENABLED=0 go build -o /preloop ./cmd/preloop

FROM node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
# npm ci verifies the lockfile integrity hashes. A plain install command
# does not, which Scorecard reports as an unpinned npmCommand.
COPY examples/claude-app-verification/package.json examples/claude-app-verification/package-lock.json /opt/claude-code/
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm ci --omit=dev --prefix /opt/claude-code \
    && mkdir -p /usr/local/lib \
    && rm -rf /usr/local/lib/node_modules \
    && cp -a /opt/claude-code/node_modules /usr/local/lib/node_modules \
    && ln -s /usr/local/lib/node_modules/.bin/claude /usr/local/bin/claude \
    && rm -rf /opt/claude-code \
    && claude --version
COPY --from=cli /preloop /usr/local/bin/preloop
COPY examples/claude-app-verification/rig.py /rig/rig.py
USER node
ENV PRELOOP_DISABLE_TELEMETRY=true
ENTRYPOINT ["python3", "/rig/rig.py"]
