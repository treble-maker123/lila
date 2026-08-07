FROM python:3.13-slim

RUN pip install uv

WORKDIR /workspace

COPY pyproject.toml .
RUN uv pip install --system -e .

COPY src/ src/

ENV OLLAMA_URL=http://host.docker.internal:11434

CMD ["python", "-m", "src.main"]
