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
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/version', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "mmbot.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
