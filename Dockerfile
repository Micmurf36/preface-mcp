FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so Docker can cache this layer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY *.py ./
COPY dashboard.html ./

# Persistent data lives in a volume at /data
RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "api.py"]
