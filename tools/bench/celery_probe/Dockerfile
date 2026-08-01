# One image for the worker and the driver; they differ only in what they run.
FROM python:3.12-slim
RUN pip install --no-cache-dir "celery[redis]>=5.3" pymongo redis
WORKDIR /app
COPY tasks.py drive.py ./
