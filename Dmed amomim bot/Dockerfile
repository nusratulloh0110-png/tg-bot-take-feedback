FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY . /app
RUN pip install --no-cache-dir .

CMD ["sh", "-c", "alembic upgrade head && python -m app.main"]

