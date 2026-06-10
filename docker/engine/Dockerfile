FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     APP_HOME=/app     PYTHONPATH=/app/src     ENGINE_HEALTH_DIR=/app/run/mmbot     ENGINE_HEARTBEAT_INTERVAL_SECONDS=60     ENGINE_MAX_HEALTH_AGE_SECONDS=180

WORKDIR ${APP_HOME}

RUN groupadd --system app && useradd --system --gid app --home-dir ${APP_HOME} app

COPY requirements.txt ${APP_HOME}/requirements.txt
RUN pip install --no-cache-dir --requirement ${APP_HOME}/requirements.txt

COPY scripts/ ${APP_HOME}/scripts/
COPY src/ ${APP_HOME}/src/
COPY database/ ${APP_HOME}/database/

RUN chmod +x ${APP_HOME}/scripts/validate_env.py && chown -R app:app ${APP_HOME}

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3     CMD python -m mmbot.cli.engine health --component-name "${COMPONENT_NAME}" --health-dir "${ENGINE_HEALTH_DIR}" --max-health-age-seconds "${ENGINE_MAX_HEALTH_AGE_SECONDS}"

CMD ["python", "-m", "mmbot.cli.engine", "run"]
