# Fresnel

The relay behind Lighthouse. Fresnel fetches public feeds on a schedule, decides each one's status, caches the last good reading on disk, and serves everything as JSON. It renders nothing; the board does that.

This folder holds the Python package. The documentation lives in [`../docs`](../docs):

- [Install and update](../docs/install.md)
- [Configuration](../docs/configuration.md) (and [`config.example.yaml`](config.example.yaml))
- [Sources](../docs/sources.md)
- [The daily brief](../docs/brief.md)
- [Hosting](../docs/hosting.md)
- [Alerts](../docs/alerts.md)
- [Commands](../docs/cli.md)
- [API](../docs/api.md)
- [Privacy and security](../docs/privacy-security.md)
- [Troubleshooting](../docs/troubleshooting.md)

Quick start for development or a laptop trial:

```
pip install -e '.[dev]'
fresnel init       # writes ./config.yaml
fresnel run        # board at http://127.0.0.1:8787/
pytest
```

Envelope and push schemas are in [`../contract`](../contract).
