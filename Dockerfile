FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/
COPY tests/ ./tests/
COPY examples/ ./examples/

RUN pip install --no-cache-dir .

# Default: run against the bundled demo config, which points at the
# in-container mock Calculator SOAP service (see docker-compose.yml,
# which starts that mock service as a separate container named
# "demo-soap-service" on the same network).
ENV CONFIG_PATH=/app/examples/soap/config.calculator.yaml

CMD ["sh", "-c", "legacy2mcp run --config ${CONFIG_PATH}"]
