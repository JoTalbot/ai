FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AEGIS_AUDIT_DB=/var/lib/aegis/audit.db

RUN groupadd --system --gid 10001 aegis \
    && useradd --system --uid 10001 --gid aegis --home-dir /home/aegis aegis \
    && mkdir -p /var/lib/aegis \
    && chown -R 10001:10001 /var/lib/aegis

WORKDIR /app
COPY dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl \
    && rm -f /tmp/*.whl

USER 10001:10001
VOLUME ["/var/lib/aegis"]
ENTRYPOINT ["aegis"]
CMD ["doctor"]
