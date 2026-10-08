FROM node:22-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "5173"]
