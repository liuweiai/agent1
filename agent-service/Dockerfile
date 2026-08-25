FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml ./
COPY src/factorypilot/__init__.py ./src/factorypilot/__init__.py
RUN --mount=type=cache,target=/root/.cache/pip pip install ".[llm]"
COPY src ./src
RUN pip install --no-deps .

EXPOSE 8000
CMD ["uvicorn", "factorypilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
