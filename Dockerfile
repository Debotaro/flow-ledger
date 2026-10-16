FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FLOW_DB=/app/data/flowledger.db FLOW_DEMO_MODE=0
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY flowledger ./flowledger
COPY reports ./reports
RUN pip install --no-cache-dir -c requirements.lock . && groupadd -g 10001 app && useradd -u 10001 -g app -M app && mkdir -p /app/data && chown -R app:app /app
USER 10001:10001
EXPOSE 8012
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8012/api/health', timeout=3)"
CMD ["python", "-m", "flowledger", "--host", "0.0.0.0", "--port", "8012"]
