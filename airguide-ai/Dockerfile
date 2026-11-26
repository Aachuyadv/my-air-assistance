FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV HOST=0.0.0.0
ENV PORT=10000

COPY . /app

EXPOSE 10000

CMD ["python", "-m", "airguide.server"]
