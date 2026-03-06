FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir -e .

EXPOSE 8100

CMD ["coinglass-mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "8100"]
