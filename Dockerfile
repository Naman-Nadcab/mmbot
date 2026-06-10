FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     APP_HOME=/app

WORKDIR ${APP_HOME}

RUN groupadd --system app && useradd --system --gid app --home-dir ${APP_HOME} app

COPY scripts/ ${APP_HOME}/scripts/
COPY services/backend/ ${APP_HOME}/services/backend/

RUN chmod +x ${APP_HOME}/scripts/validate_env.py && chown -R app:app ${APP_HOME}

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD python -c "import socket; s=socket.create_connection(('127.0.0.1', 8000), 3); s.close()"

CMD ["python", "-m", "http.server", "8000", "--directory", "services/backend"]
