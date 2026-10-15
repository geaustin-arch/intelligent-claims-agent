FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir setuptools==82.0.1 && \
    pip install --no-cache-dir --require-hashes -r requirements.lock && \
    pip install --no-cache-dir --no-deps --no-build-isolation .
RUN mkdir /data && chown 65532:65532 /data && chmod 700 /data
USER 65532:65532
EXPOSE 8000
CMD ["uvicorn", "claims_agent.app:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "32", "--timeout-keep-alive", "5"]
