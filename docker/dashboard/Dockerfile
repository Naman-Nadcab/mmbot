FROM nginx:1.27-alpine AS runtime

COPY services/dashboard/ /usr/share/nginx/html/

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3     CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
