# NFM-415: Production Reverse Proxy Configuration

**Parent:** NFM-412 (blocked)
**Type:** Infrastructure
**Priority:** High (unblocks API access via domain)
**Depends on:** NFM-413 (server must exist first)

## Objective

Configure reverse proxy (nginx or Caddy) to serve:
- Web app (Next.js/Vercel) at root `https://nucpot.dpdns.org/`
- API at `https://nucpot.dpdns.org/api/` → FastAPI backend on `localhost:8000`

## Option A: Nginx

### Configuration File
```nginx
server {
    listen 443 ssl http2;
    server_name nucpot.dpdns.org;

    ssl_certificate /etc/ssl/certs/nucpot.crt;
    ssl_certificate_key /etc/ssl/private/nucpot.key;

    # API reverse proxy
    location /api/ {
        proxy_pass http://localhost:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts for long-running requests
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # Web app (or reverse proxy to Vercel)
    location / {
        # Option 1: Serve static files from Next.js build
        root /var/www/nucpot/web/.next;
        try_files $uri $uri/ /index.html;

        # Option 2: Reverse proxy to Vercel
        # proxy_pass https://nucpot.vercel.app;
    }

    # Health check endpoint
    location /health {
        proxy_pass http://localhost:8000/health;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name nucpot.dpdns.org;
    return 301 https://$server_name$request_uri;
}
```

### Setup Steps
1. Install nginx: `sudo apt install nginx`
2. Copy config to `/etc/nginx/sites-available/nucpot`
3. Enable site: `sudo ln -s /etc/nginx/sites-available/nucpot /etc/nginx/sites-enabled/`
4. Test config: `sudo nginx -t`
5. Reload nginx: `sudo systemctl reload nginx`

## Option B: Caddy (Simpler, auto HTTPS)

### Caddyfile
```
nucpot.dpdns.org {
    # API reverse proxy
    handle /api/* {
        reverse_proxy localhost:8000
    }

    # Health check
    handle /health {
        reverse_proxy localhost:8000
    }

    # Web app (or reverse proxy to Vercel)
    handle {
        # Option 1: Serve static files
        root * /var/www/nucpot/web/.next
        file_server
        try_files {path} /index.html

        # Option 2: Reverse proxy to Vercel
        # reverse_proxy https://nucpot.vercel.app
    }
}
```

### Setup Steps
1. Install Caddy: `sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https`
2. Add Caddy repo and install
3. Copy Caddyfile to `/etc/caddy/Caddyfile`
4. Validate config: `sudo caddy validate --config /etc/caddy/Caddyfile`
5. Restart Caddy: `sudo systemctl restart caddy`

## SSL/TLS Considerations

- **Nginx**: Requires SSL certificate (Let's Encrypt recommended via certbot)
- **Caddy**: Automatic HTTPS with built-in Let's Encrypt integration

## Firewall Configuration

Ensure ports are open:
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## Definition of Done

- [ ] Reverse proxy installed and configured
- [ ] SSL/TLS working (HTTPS accessible)
- [ ] API requests to `https://nucpot.dpdns.org/api/` reach FastAPI backend
- [ ] Web requests to `https://nucpot.dpdns.org/` serve Next.js app
- [ ] Health check `https://nucpot.dpdns.org/health` returns 200
- [ ] HTTP redirects to HTTPS

## Verification

```bash
# Test API
curl https://nucpot.dpdns.org/api/health

# Test web
curl -I https://nucpot.dpdns.org/

# Test HTTP→HTTPS redirect
curl -I http://nucpot.dpdns.org
```

## Estimated Effort

2-4 hours (including SSL setup)

## Owner

Lead Engineer / DevOps
