# Frontend образ: сборка статики (Vite) + отдача через nginx, который также
# проксирует /api и /health на backend (same-origin — CORS не нужен).
FROM node:22-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
# Пустой VITE_API_URL → axios baseURL='' → запросы идут на /api того же origin
# (nginx проксирует на backend). Vite отдаёт приоритет реальной env-переменной
# над .env-файлами, поэтому build-arg перекрывает .env.development.
ARG VITE_API_URL=
ENV VITE_API_URL=$VITE_API_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
