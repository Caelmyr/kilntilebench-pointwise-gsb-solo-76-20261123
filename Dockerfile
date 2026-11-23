ARG KILNBENCH_RUNTIME_IMAGE=python:3.12-slim
FROM ${KILNBENCH_RUNTIME_IMAGE}
USER root
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 PORT=8000 KILNBENCH_DATA=/var/lib/kilnbench
RUN if [ -d /app ]; then rm -rf /app; fi
WORKDIR /opt/kilnbench
COPY requirements.txt scripts/check_dependencies.py ./
RUN (python check_dependencies.py || python -m pip install --disable-pip-version-check --no-cache-dir --only-binary=:all: -r requirements.txt) \
    && python check_dependencies.py && python -m pip check
COPY kilnbench ./kilnbench
COPY image_lab ./image_lab
COPY static ./static
COPY templates ./templates
RUN useradd -U -u 11533 kilnbench && install -d -o kilnbench -g kilnbench /var/lib/kilnbench
USER kilnbench
EXPOSE 8000
HEALTHCHECK NONE
CMD ["python", "-m", "kilnbench"]
