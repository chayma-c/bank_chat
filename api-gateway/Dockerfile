FROM nginx:alpine

# Supprimer la config par défaut
RUN rm /etc/nginx/conf.d/default.conf

# Copier notre configuration
COPY nginx.conf /etc/nginx/conf.d/gateway.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
