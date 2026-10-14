# Hosting

By default Fresnel listens on `127.0.0.1:8787` and serves the board at `/`. That is reachable only from the server itself. To open it from your phone or laptop, put a reverse proxy with HTTPS in front. The relay speaks plain HTTP and should never be exposed directly to the internet.

You need a domain name pointing at the server, nginx, and a certificate (Let's Encrypt's `certbot` is the usual way).

```
sudo apt install nginx certbot python3-certbot-nginx
sudo certbot --nginx -d lighthouse.example.org
```

## Option A: under a path on a site you already have

If the domain already serves something else on port 443, add Fresnel under a path such as `/fresnel`:

```
fresnel proxy-snippet --path /fresnel
```

It prints a block to put inside your existing HTTPS `server { ... }`:

```nginx
location = /fresnel { return 301 /fresnel/; }

location /fresnel/ {
    proxy_pass http://127.0.0.1:8787/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 256k;
}
```

Then:

```
sudo nginx -t && sudo systemctl reload nginx
```

and set `server.public_url: https://lighthouse.example.org/fresnel` in `config.yaml`. The board is at `https://lighthouse.example.org/fresnel/`. The trailing slash on `proxy_pass` matters: it strips the `/fresnel` prefix.

`client_max_body_size 256k` lets pushed briefs through. nginx's default is 1 MB, which is fine, but a tighter limit than Fresnel's own (`push.brief.max_bytes`, 200000) would turn into a confusing 413.

## Option B: its own port

If a path is awkward (the domain's main site is managed by something else), give Lighthouse its own port. Use the same certificate as the domain:

```nginx
server {
    listen 47831 ssl;
    listen [::]:47831 ssl;
    server_name lighthouse.example.org;

    ssl_certificate     /etc/letsencrypt/live/lighthouse.example.org/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lighthouse.example.org/privkey.pem;

    client_max_body_size 256k;

    location / {
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Open that port in the firewall (see below). The address is `https://lighthouse.example.org:47831/`, and `public_url` is that address. Choose any unused port above 1024; an unusual number avoids the constant scanning that ports 80, 443 and 8443 attract, though it is not a secret.

## Firewall

Two firewalls can apply, and both must allow the port:

- The one on the machine (`ufw`, `iptables`, `nftables`): `sudo ufw allow 443/tcp` (or your port).
- The one your hosting provider runs in front of the machine, in their control panel.

Fresnel's own port (8787) should not be open to the outside. Only nginx talks to it.

**Outgoing** traffic needs nothing, but if the provider also restricts it, Fresnel must reach the feed hosts over HTTPS (443).

## Who can see it

The board and the API are read-only and carry no secrets, but they do show your area, your configured airports and your brief. They have no login. If that is too public, restrict the nginx location with HTTP basic auth (`auth_basic`), an IP allow-list (`allow` / `deny`), or put it behind a VPN or tailnet. The push endpoint is separate: it always needs a token.

If you add basic auth in front of everything, a job that pushes the brief will need an exception for `/v1/push/`, since it authenticates with its token instead.

## Hosting the board somewhere else

The board is a static folder and can be served from anywhere (another nginx site, GitHub Pages, S3, Netlify). Copy `board/`, then:

1. In your copy of `config.js`, point it at the relay:
   ```js
   window.LIGHTHOUSE_RELAY = "https://fresnel.example.org";
   ```
2. In Fresnel's `config.yaml`, allow the board's origin and restart:
   ```yaml
   server:
     cors_origins: ["https://board.example.org"]
   ```
3. Serve both over HTTPS. A board on HTTPS cannot call a relay on plain HTTP; browsers block it.

If you do not want the relay to serve a board at all, leave out `server.board_dir`; `/` then answers with a small JSON description.

A board on a separate host holds no secrets. The relay only answers `GET` to other origins. See `board/README.md`.

## Reading the logs

```
sudo tail -n 30 /var/log/nginx/error.log
sudo tail -n 30 /var/log/nginx/access.log
```

A request that never appears in the access log never reached nginx: look at the firewall, DNS, or the provider's network rules. A request that appears with `502` means nginx could not reach Fresnel: check `systemctl status fresnel`.

## Renewing certificates

`certbot` installs a timer that renews automatically. Test it with `sudo certbot renew --dry-run`. If nginx serves Lighthouse on a custom port, it uses the same certificate files, and nginx must be reloaded to pick up a renewed one; `certbot --nginx` does that for you.
