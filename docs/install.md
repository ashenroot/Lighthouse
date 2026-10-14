# Install and update

## Requirements

- Linux with systemd (Ubuntu 22.04+, Debian 12+ and similar). Without systemd, see [Without the installer](#without-the-installer).
- Python 3.10 or newer, with `python3-venv` (`sudo apt install python3 python3-venv git`).
- Outbound HTTPS to the feeds you enable. If your host's network is blocked by a publisher, see [Sources](sources.md#when-a-feed-blocks-your-server).
- About 100 MB of disk and under 100 MB of memory for the relay once running (installing the Python packages needs more). See [Small servers](#small-servers) if the machine has under 1 GB.

## The installer

```
curl -fsSLO https://raw.githubusercontent.com/ashenroot/Lighthouse/main/install.sh
sudo bash install.sh
```

Download first and run second. The installer asks questions, and a pipe straight into `bash` leaves it with no keyboard to ask on.

It asks for:

- **Latitude and longitude** of the place to watch, in decimal degrees (for example `39.10` and `-94.58`, with west and south as negative numbers). The state and county are looked up from these through the US Census area service (`geo.fcc.gov`), which is the only time the installer sends your coordinates anywhere.
- **Airport codes** to watch for delays, such as `MCI`. Leave blank to skip.
- **A contact email**, which goes into the User-Agent that identifies your relay to the weather service. NWS asks for this. It is only sent to that service.

Then it:

1. Creates a service account named `lighthouse` with no login shell.
2. Clones the code to `/opt/lighthouse/app` and builds a Python environment at `/opt/lighthouse/app/fresnel/.venv`.
3. Links `/usr/local/bin/fresnel` so the `fresnel` command works for every user.
4. Writes `config.yaml` and a `data/` folder in `/opt/lighthouse/app/fresnel/`.
5. Installs and starts a `fresnel` systemd service, and waits until `/v1/health` answers.

Everything the relay needs, including your config, lives in that one folder. It is safe to run the installer again: it keeps an existing config and its tokens.

### Unattended install

Give the answers up front and it never asks:

```
sudo bash install.sh --init-args "--yes --lat 39.10 --lon -94.58 --airports MCI --email you@example.org"
```

### Installer options

| Option | Default | Meaning |
|---|---|---|
| `--dir DIR` | `/opt/lighthouse` | Where the code and environment go. |
| `--user NAME` | `lighthouse` | The service account. |
| `--config-dir DIR` | the app folder | Where `config.yaml` lives. |
| `--data-dir DIR` | `data/` in the app folder | Where the relay keeps its state. |
| `--repo URL` | the GitHub repository | Install from a fork. |
| `--ref NAME` | `main` | A branch or tag. |
| `--no-systemd` | off | Install and configure, but do not create or start a service. |
| `--init-args "..."` | none | Extra arguments for `fresnel init`. With `--yes` it skips the questions. |

### What the service looks like

The installer writes `/etc/systemd/system/fresnel.service`:

```ini
[Service]
User=lighthouse
ExecStart=/opt/lighthouse/app/fresnel/.venv/bin/fresnel --config /opt/lighthouse/app/fresnel/config.yaml run
Restart=always
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/lighthouse/app/fresnel/data
```

The relay can write only to its data folder and cannot gain privileges. If you edit this file by hand, run `sudo systemctl daemon-reload` afterwards. Do not point `EnvironmentFile=` at a file that does not exist, or the service will not start (see [Troubleshooting](troubleshooting.md#the-service-will-not-start)).

Useful commands:

```
systemctl status fresnel               # is it running
journalctl -u fresnel -f               # live log
journalctl -u fresnel -n 50 --no-pager # the last 50 lines
sudo systemctl restart fresnel         # after editing config.yaml
```

Restart after every config change. A mistake in the file stops the service from starting, and the log says which line.

## Without the installer

For a laptop, a container, or a machine without systemd:

```
git clone https://github.com/ashenroot/Lighthouse.git
cd Lighthouse/fresnel
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
fresnel init        # asks the same questions, writes ./config.yaml
fresnel run
```

Open `http://127.0.0.1:8787/`. `fresnel init` turns on serving the board; see `server.board_dir` in [Configuration](configuration.md#server). To keep it running without systemd, use whatever your system has (`tmux`, `supervisord`, a launchd or init script) and run `fresnel run`.

## Updating

```
sudo fresnel update
```

This pulls the latest code, reinstalls dependencies, and restarts the service. It prints each step. `fresnel update --check` lists what is available without changing anything. New settings always have defaults, so your config keeps working.

If `fresnel update` cannot run, do the same steps by hand. Run git as the owner of the folder, not as root:

```
cd /opt/lighthouse/app
sudo -u lighthouse git pull
sudo -u lighthouse /opt/lighthouse/app/fresnel/.venv/bin/pip install -e /opt/lighthouse/app/fresnel
sudo systemctl restart fresnel
```

`git` refusing with "dubious ownership" means it was run as root in a folder the `lighthouse` account owns. Use `sudo -u lighthouse git ...`.

## Small servers

A machine with under 1 GB of memory can run out while installing Python packages, which freezes or reboots it. Add swap first. This is a one-time step:

```
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -m
```

If you install packages yourself, `pip install --no-cache-dir` uses less memory.

## Moving the config or data

Both paths are settings, so you can keep them elsewhere (for example `/etc/lighthouse/config.yaml` and `/var/lib/lighthouse`). The installer's `--config-dir` and `--data-dir` do this. Relative paths in `config.yaml` are relative to the file. The service account must be able to read the config and write the data folder.

## Backups

Everything worth keeping is the `data/` folder and `config.yaml`. The data folder holds the cached readings, the archive of daily briefs, push tokens (as hashes), and the mailbox password if you use that. Losing it costs you the brief archive and tokens, nothing else; the feeds refill within minutes.

## Uninstalling

```
sudo systemctl disable --now fresnel
sudo rm /etc/systemd/system/fresnel.service /usr/local/bin/fresnel
sudo systemctl daemon-reload
sudo rm -rf /opt/lighthouse
sudo userdel lighthouse
```

Then remove any nginx configuration and firewall rules you added for it.
