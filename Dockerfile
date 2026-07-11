FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data

ENV DB_PATH=/data/suggestions.db

EXPOSE 8555

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8555"]
