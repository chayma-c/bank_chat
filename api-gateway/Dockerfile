FROM nginx:alpine

# Supprimer la config par défaut
RUN rm /etc/nginx/conf.d/default.conf

# Installer wget pour le health check
RUN apk add --no-cache wget

# Copier notre configuration
COPY nginx.conf /etc/nginx/conf.d/gateway.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
