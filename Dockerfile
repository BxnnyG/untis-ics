# Build the wheel in a throwaway stage so no build tooling ends up in the
# final image.
FROM python:3.12-slim AS build

WORKDIR /src
RUN pip install --no-cache-dir build

COPY pyproject.toml README.md LICENSE ./
COPY untis_calendar ./untis_calendar
RUN python -m build --wheel --outdir /dist


FROM python:3.12-slim

LABEL org.opencontainers.image.title="untis-ics" \
      org.opencontainers.image.description="Serve WebUntis timetables as ICS calendar feeds" \
      org.opencontainers.image.source="https://github.com/BxnnyG/untis-ics" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UNTIS_APP_OUTPUT_DIR=/data

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Run unprivileged. /data holds the generated .ics files and must be writable.
RUN useradd --system --create-home --uid 10001 untis \
    && mkdir -p /data \
    && chown untis:untis /data
USER untis

WORKDIR /app
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5).status==200 else 1)"

ENTRYPOINT ["untis-ics"]
CMD ["serve", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "8080"]
