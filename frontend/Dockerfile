# Étape 1 : Build Angular
FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build --configuration=production

# Étape 2 : Servir avec Nginx
FROM nginx:alpine

COPY --from=builder /app/dist/*/browser /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80