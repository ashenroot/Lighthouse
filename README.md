# Lighthouse

A community-service preparedness board: weather, tropical, space weather, cyber and local declarations in one place, for any location.

- **Fresnel** (`fresnel/`): the relay. Fetches, scores and serves the data as JSON.
- **Lighthouse board** (`board/`): a static dashboard that displays it. See [`board/README.md`](board/README.md).
- **Keeper's Log**: a daily brief produced by an outside job and pushed to Fresnel.

The board is static and can be hosted anywhere. Fresnel can also serve it, which suits a single-server setup. Install on a Linux server with `sudo ./install.sh`. See [`fresnel/README.md`](fresnel/README.md) for commands, updating and publishing.

## Install

On a Linux server with Python 3.10 or newer (Ubuntu 22.04+, Debian 12+):

```
curl -fsSLO https://raw.githubusercontent.com/ashenroot/Lighthouse/main/install.sh
sudo bash install.sh
```

It asks for your coordinates (and an optional airport code and contact email), creates a service account, installs Fresnel, starts it as a `fresnel` service, and prints the next steps. For an unattended install, pass the answers up front:

```
sudo bash install.sh --init-args "--yes --lat 39.10 --lon -94.58 --email you@example.org"
```

Update later with `sudo fresnel update`. To try it without a server, see [`fresnel/README.md`](fresnel/README.md).

## Getting the daily brief in

The Keeper's Log is a briefing written by something outside Lighthouse, such as a scheduled job. Pick the way that suits where that job can reach:

| If the job can… | Use | Setup |
|---|---|---|
| reach your relay over HTTPS | **push** | `fresnel brief-step` prints the step to give the job |
| only send email (Gmail) | **mailbox** | a `mailbox:` section and `fresnel mailbox set-password` |
| reach a public ntfy server | **ntfy** | `brief_ntfy:` and `fresnel brief-step --ntfy` |

Details are in [`fresnel/README.md`](fresnel/README.md#push). Number the sections of the brief and rate each one (`QUIET`, `NOTABLE`, `ELEVATED`, `CRITICAL`) and the board shows them as a list you can open one at a time.

## Use it, fork it

Lighthouse is published for anyone to run and adapt, under the [MIT license](LICENSE). It is maintained by one person and is not open to outside pull requests or feature requests; fork it and make it yours.
