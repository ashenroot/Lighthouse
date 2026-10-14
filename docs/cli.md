# Commands

`fresnel` is the command-line tool. The installer links it to `/usr/local/bin/fresnel`. Run `fresnel --help`, or `fresnel COMMAND --help`, for the built-in summary.

Commands that read or write the installed config, data or tokens should be run with `sudo` so they act on the same files as the service. Every command that reads the config accepts `--config PATH`; otherwise the file is found as described in [Configuration](configuration.md#where-the-file-is-found).

`fresnel --version` prints the version.

## `fresnel run`

Runs the relay. This is what the service runs, and what plain `fresnel` does. `--host` and `--port` override the config for one run.

## `fresnel check`

Validates the config and shows what is on: which file was read, the location (rounded), listen address, data folder, whether the board is served, the public URL, enabled and skipped sources, push tokens, whether alerts are on, and how the brief arrives (mailbox, ntfy, or push only). Run it after every config edit. `fresnel check --print port` (or `host`, `data_dir`) prints just that value for scripts.

## `fresnel init`

Creates a config by asking questions. The installer runs it for you.

| Option | Meaning |
|---|---|
| `--yes` | Never ask; use the flags and defaults. |
| `--lat`, `--lon` | Coordinates. The state and county are looked up from them. |
| `--name` | Label for the board header. |
| `--email` | Contact address for the User-Agent. |
| `--airports` | Comma-separated airport codes. |
| `--public-url` | The address people use to reach the relay. |
| `--ntfy-topic` | Topic for alerts. Pass `auto` to generate one. |
| `--port` | Port to listen on (default 8787). |
| `--data-dir` | Data folder (default `./data`). |
| `--no-board` | Do not have the relay serve the board. |
| `--out` | Where to write the file. As root, the default is `config.yaml` in the app folder; otherwise `./config.yaml`. |
| `--owner` | The account that runs the relay; it will own the config and data. |
| `--force` | Replace an existing file. Without it, `init` refuses. |

## `fresnel token`

Push tokens let an outside job write to a slot. They are stored as hashes; the real value is shown once.

```
fresnel token add NAME --slots brief      # slots: brief, context, or * for all
fresnel token list
fresnel token revoke NAME
```

Changes apply immediately; no restart is needed.

## `fresnel brief-step`

Prints the text to paste into a briefing prompt so the job delivers its brief. See [The daily brief](brief.md).

| Option | Meaning |
|---|---|
| (none) | Creates a token `brief-job` for the `brief` slot and fills in the URL. |
| `--url URL` | The relay address; defaults to `server.public_url`. |
| `--token VALUE` | Use an existing token instead of creating one. |
| `--rotate` | Revoke `brief-job` first and create a new one. |
| `--ntfy` | Print the step that posts to your `brief_ntfy` topic instead. |
| `--out FILE` | Write to a file (mode 600) instead of the screen. |

## `fresnel mailbox`

```
fresnel mailbox set-password    # asks for the password without echoing it; saves it in the data folder, mode 600
fresnel mailbox test            # logs in and shows what would be published, or why messages were skipped
```

See [The daily brief](brief.md#option-2-read-it-from-a-mailbox).

## `fresnel ntfy-topic`

Prints a new random topic name for `brief_ntfy.topic`. It changes nothing.

## `fresnel proxy-snippet`

Prints an nginx block that publishes the relay under a path. `--path` sets the path (default `/fresnel`). See [Hosting](hosting.md).

## `fresnel update`

```
sudo fresnel update            # pull, reinstall dependencies, restart the service
fresnel update --check         # only list what is available
```

It runs git as the folder's owner, so root is fine. It needs the install to be a git checkout (the installer makes one). See [Install](install.md#updating).
