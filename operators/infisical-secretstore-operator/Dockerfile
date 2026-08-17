FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

USER 1001
# --standalone: single replica, no kopfpeerings CRD installed - matches actual
# deployment topology, no cross-instance coordination needed or available.
CMD ["kopf", "run", "--standalone", "--liveness=http://0.0.0.0:8080/healthz", "/app/main.py"]
