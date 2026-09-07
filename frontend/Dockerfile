# 前端静态构建 + Nginx 同源反代镜像（后端方案 文件变更清单：frontend/Dockerfile）
# 构建产物 /app/dist 由 Nginx 提供静态资源；/api 与 SSE 反代配置见 docker/nginx.conf

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
