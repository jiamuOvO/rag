FROM python:3.11-slim
ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5
WORKDIR /app
COPY requirements.lock.txt pyproject.toml ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.lock.txt \
    && pip uninstall -y opencv-python \
    && pip install --no-cache-dir opencv-python-headless==4.11.0.86 \
    && pip install --no-deps -e .
EXPOSE 8000
CMD ["python", "-m", "rag.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
