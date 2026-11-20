# FiscalMatch · portal (React 19 + Vite) servido por nginx
# ---------------------------------------------------------------- build
FROM node:20-alpine AS construccion

WORKDIR /app

# `npm ci` cuando existe el lockfile; si no, `npm install` (repo recien clonado).
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---------------------------------------------------------------- runtime
# nginx oficial SIN privilegios (usuario 101): el proceso nunca corre como root.
FROM nginxinc/nginx-unprivileged:1.31-alpine AS servidor

# La configuracion se escribe como root durante la construccion y se devuelve
# el control al usuario sin privilegios antes de arrancar.
USER root

# El portal es una SPA: cualquier ruta profunda debe caer en index.html,
# y /api se reenvia al servicio de la API dentro de la red de compose.
RUN printf '%s\n' \
    'server {' \
    '  listen 5173;' \
    '  server_name _;' \
    '  root /usr/share/nginx/html;' \
    '  index index.html;' \
    '  gzip on;' \
    '  gzip_types text/css application/javascript application/json image/svg+xml;' \
    '' \
    '  location /assets/ {' \
    '    expires 30d;' \
    '    add_header Cache-Control "public, immutable";' \
    '  }' \
    '' \
    '  location /api/ {' \
    '    proxy_pass http://api:8000;' \
    '    proxy_http_version 1.1;' \
    '    proxy_set_header Host $host;' \
    '    proxy_set_header X-Real-IP $remote_addr;' \
    '    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;' \
    '    proxy_set_header X-Forwarded-Proto $scheme;' \
    '    client_max_body_size 20m;' \
    '  }' \
    '' \
    '  location / {' \
    '    try_files $uri $uri/ /index.html;' \
    '  }' \
    '}' \
    > /etc/nginx/conf.d/default.conf

COPY --from=construccion /app/dist /usr/share/nginx/html

USER 101

EXPOSE 5173

CMD ["nginx", "-g", "daemon off;"]
