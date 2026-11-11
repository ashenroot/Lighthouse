# API keys

Most sources need no key. A few providers ask for a free one, and the sources that use them stay off until you set it. Everything else keeps working without any key.

## Setting a key

1. Get a free key from the provider (`fresnel key list` shows the signup link for each).
2. On the server, run `sudo fresnel key set <name>`. It asks for the key without echoing it, or reads it from a pipe.
3. Run `sudo fresnel key test <name>` to fetch once from every source that uses it.

No restart is needed. A new key is picked up within a minute.

| Command | What it does |
|---|---|
| `fresnel key list` | Shows each key as set or not set, and which sources use it. |
| `fresnel key set <name>` | Saves a key. |
| `fresnel key test <name>` | Fetches once from each source that uses the key and shows the result or the error. |
| `fresnel key remove <name>` | Deletes the saved key. |

| Name | Provider | Sign up |
|---|---|---|
| `eia` | U.S. Energy Information Administration | https://www.eia.gov/opendata/register.php |
| `airnow` | EPA AirNow | https://docs.airnowapi.org/account/request/ |

## Where keys live

A key is saved in the data folder, in `keys/<name>`, mode 600, owned by the service account, like the mailbox password. It is never written to `config.yaml`, never part of the repository, and never sent to the board. The snapshot, the health endpoint, error text and the logs do not contain it. A key is only accepted if it looks like one (8 to 200 letters, digits, dashes, underscores, dots), because it is placed in a URL.

For containers, set the environment variable `FRESNEL_KEY_<NAME>` (for example `FRESNEL_KEY_EIA`) instead. It overrides the file.

## What the board shows

- **No key set:** the source is not shown as an empty or unknown panel. It is listed once, under **Setup** at the bottom of the board, with the command to run and the signup link. A forked install with no keys works as before and shows that one line.
- **Key refused by the provider:** the source fails visibly. Its panel shows unknown, with a message such as "EIA rejected the key (HTTP 403)". Fresnel tries again every five minutes, so a corrected key works soon.
- **Key set and working:** the source behaves like any other.

`fresnel check` shows how many sources are waiting for a key.
