FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.9.5

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups --group service --no-install-project

COPY ledger/ ./ledger/
WORKDIR /app/ledger

EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4 --access-logfile -"]
