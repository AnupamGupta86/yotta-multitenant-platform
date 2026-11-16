FROM python:3.12-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir pip==26.3 'setuptools>=78.1.1,<81' && \
    python -m pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
USER 10001:10001
EXPOSE 3000
CMD ["python", "-m", "src.main"]
