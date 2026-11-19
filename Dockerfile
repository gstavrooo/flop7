# Spec section 3: one small image served by uvicorn. The deploy workflow builds
# this image, smoke-tests it and pushes the tested tag to GHCR.
FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATABASE_URL=sqlite:////data/flop7.db \
    MIGRATE_ON_START=1

# Apply Debian security fixes to the pinned base (Trivy fails on fixable
# HIGH/CRITICAL findings in the container-test gate).
RUN apt-get update \
 && apt-get -y --no-install-recommends upgrade \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.txt \
 && python -m pip uninstall -y pip setuptools

COPY app ./app

RUN mkdir -p /data && chown -R nobody:nogroup /app /data
USER nobody

EXPOSE 8080
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"${PORT}\""]
