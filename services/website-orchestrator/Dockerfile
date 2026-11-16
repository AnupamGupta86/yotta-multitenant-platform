FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir pip==26.3 'setuptools>=78.1.1,<81' && \
    python -m pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY publish_event.py ./publish_event.py
# Copied files keep the build machine's permissions: make them readable by the app user (uid 10001)
RUN chmod -R a+rX /app && \
    groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
USER 10001:10001
CMD ["python", "-m", "src.consumer"]
