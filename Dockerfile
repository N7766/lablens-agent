FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY lablens ./lablens
RUN pip install --no-cache-dir .
USER 10001
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "lablens.api:app", "--host", "0.0.0.0", "--port", "8765"]
