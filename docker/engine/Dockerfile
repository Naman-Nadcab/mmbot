FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     APP_HOME=/app

WORKDIR ${APP_HOME}

RUN groupadd --system app && useradd --system --gid app --home-dir ${APP_HOME} app

COPY scripts/ ${APP_HOME}/scripts/
COPY services/ ${APP_HOME}/services/

RUN chmod +x ${APP_HOME}/scripts/validate_env.py && chown -R app:app ${APP_HOME}

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD python ${APP_HOME}/scripts/validate_env.py >/dev/null

CMD ["python", "-c", "import time; print('engine boundary placeholder started', flush=True); time.sleep(31536000)"]
