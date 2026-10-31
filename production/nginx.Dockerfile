FROM nginx:1.28-alpine
COPY production/nginx.conf /etc/nginx/nginx.conf
COPY production/http-bootstrap.conf production/https.conf production/proxy-entrypoint.sh /opt/onepost/
ENTRYPOINT ["sh", "/opt/onepost/proxy-entrypoint.sh"]
