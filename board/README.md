# Lighthouse board

A static page that displays Fresnel's `/v1/snapshot`. No build step, no dependencies, no third-party requests.

## Run

Easiest: let Fresnel serve it. The installer and `fresnel init` turn this on. By hand, in `config.yaml`:

```yaml
server:
  board_dir: ../board
```

and open `http://127.0.0.1:8787/`. The board then answers under a Content-Security-Policy that allows only its own origin.

## Host it on its own

The board is a folder of static files and needs nothing from the relay's server. Copy `board/` to any web host (nginx, Apache, GitHub Pages, S3, Netlify) and point it at the relay:

1. In your copy of `config.js`, set the relay's address:
   ```js
   window.LIGHTHOUSE_RELAY = "https://fresnel.example.org";
   ```
2. In Fresnel's `config.yaml`, allow the board's origin (scheme, host and port, no path), then restart Fresnel:
   ```yaml
   server:
     cors_origins: ["https://board.example.org"]
   ```
3. Serve both over HTTPS. A board on HTTPS cannot call a relay on plain HTTP, because browsers block mixed content.

Edit the copy you deploy, so `git pull` on a checkout never conflicts with your settings. If your web host sets its own Content-Security-Policy, `connect-src` must include the relay's origin.

## Use

- Status tiles across the top show each panel's status. Click one to hide or show that panel.
- Drag a panel by its header, or use the arrows, to rearrange.
- The refresh interval (off, 1m to 1h) sits in the header. Refresh uses `If-None-Match`, so an unchanged snapshot costs a 304.
- Interval, hidden panels and order are stored in this browser's localStorage (`lighthouse.prefs`).
- Every row links to its source in a new tab. Each panel footer shows per-source freshness.
- If the relay is unreachable, the board keeps the last data and shows a banner.

## Notes

The board holds no status logic and no secrets. Everything on it was decided by Fresnel, and all content is inserted as text with only `http(s)` links.
