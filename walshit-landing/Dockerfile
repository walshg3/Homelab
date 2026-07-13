FROM --platform=linux/amd64 nginxinc/nginx-unprivileged:1.30.3-alpine@sha256:a0c30a699fa18d7bbdc6faf197742741a1f5e4631066abf18578dc8102b62c5d

COPY nginx.conf /etc/nginx/nginx.conf
COPY site/index.html /usr/share/nginx/html/index.html
COPY site/styles.css /usr/share/nginx/html/styles.css
COPY site/app.js /usr/share/nginx/html/app.js
COPY site/healthz.txt /usr/share/nginx/html/healthz.txt

USER 101:101
EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
