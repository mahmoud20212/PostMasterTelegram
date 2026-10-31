#!/bin/sh
set -eu
case "${PUBLIC_IP:-}" in ''|*[!0-9a-fA-F:.]*) echo 'PUBLIC_IP is invalid' >&2; exit 1;; esac
case "$PUBLIC_IP" in *:*) PUBLIC_ORIGIN="https://[$PUBLIC_IP]";; *) PUBLIC_ORIGIN="https://$PUBLIC_IP";; esac
export PUBLIC_ORIGIN

render() {
    template=/opt/onepost/http-bootstrap.conf
    if [ -s /etc/letsencrypt/live/onepost-ip/fullchain.pem ] && [ -s /etc/letsencrypt/live/onepost-ip/privkey.pem ]; then
        template=/opt/onepost/https.conf
    fi
    envsubst '${PUBLIC_IP} ${PUBLIC_ORIGIN}' < "$template" > /tmp/onepost-nginx.next
    if [ -f /etc/nginx/conf.d/default.conf ]; then cp /etc/nginx/conf.d/default.conf /tmp/onepost-nginx.previous; fi
    cp /tmp/onepost-nginx.next /etc/nginx/conf.d/default.conf
    if nginx -t; then return 0; fi
    if [ -f /tmp/onepost-nginx.previous ]; then cp /tmp/onepost-nginx.previous /etc/nginx/conf.d/default.conf; fi
    return 1
}

seen="$(cat /run/cert-events/reload 2>/dev/null || true)"
render
nginx -g 'daemon off;' &
nginx_pid=$!
trap 'kill -QUIT "$nginx_pid" 2>/dev/null || true; wait "$nginx_pid"; exit 0' TERM INT
# Starting with an existing certificate requires no event. Events are written
# atomically by Certbot's deploy-hook only after a successful issue/renewal.
while kill -0 "$nginx_pid" 2>/dev/null; do
    current="$(cat /run/cert-events/reload 2>/dev/null || true)"
    if [ "$current" != "$seen" ]; then
        if render && nginx -s reload; then seen="$current"; else echo 'TLS reload failed; retrying' >&2; fi
    fi
    sleep 10 &
    wait $! || true
done
wait "$nginx_pid"
