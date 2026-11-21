FROM node:24-alpine

WORKDIR /app

# 无第三方依赖，仅复制运行所需文件
COPY package.json ./
COPY src ./src
COPY public ./public

ENV PORT=8787
ENV NODE_ENV=production
ENV TZ=Asia/Shanghai

EXPOSE 8787

# 数据目录（自选、组合、日报）挂载为卷，避免容器重建后丢失
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD node -e "fetch('http://127.0.0.1:8787/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "src/server.js"]
