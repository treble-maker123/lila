FROM python:3.13-slim

RUN pip install uv

WORKDIR /workspace

COPY pyproject.toml .
# Stub package so the editable install resolves before src/ is copied; this keeps
# the dependency layer cached across source edits.
RUN mkdir -p src/loop_workflow_comparison \
    && touch src/loop_workflow_comparison/__init__.py \
    && uv pip install --system -e .

COPY src/ src/

ENV OLLAMA_URL=http://host.docker.internal:11434

CMD ["python", "-m", "loop_workflow_comparison.main"]
