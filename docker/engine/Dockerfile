FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     APP_HOME=/app     PYTHONPATH=/app/src

WORKDIR ${APP_HOME}

RUN groupadd --system app && useradd --system --gid app --home-dir ${APP_HOME} app

COPY requirements.txt ${APP_HOME}/requirements.txt
RUN pip install --no-cache-dir --requirement ${APP_HOME}/requirements.txt

COPY scripts/ ${APP_HOME}/scripts/
COPY src/ ${APP_HOME}/src/
COPY database/ ${APP_HOME}/database/

RUN chmod +x ${APP_HOME}/scripts/validate_env.py && chown -R app:app ${APP_HOME}

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD python ${APP_HOME}/scripts/validate_env.py >/dev/null

CMD ["python", "-m", "mmbot.cli.engine", "sample-quotes"]
