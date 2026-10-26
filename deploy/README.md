# Hosting LiveOption

Complete the [QFinZero and LiveOption setup guide](../docs/getting-started.md)
before hosting a public instance. These examples use a single Linux host and
contain no maintainer infrastructure settings.

## Application service

1. Place your checkout at `~/LiveOption`, install its locked
   dependencies and configure `.env` with your QFinZero endpoint, QFinZero API
   key and a long, unique `LIVEOPTION_ACCESS_TOKEN`.
2. Set `LIVEOPTION_WEB_URL=https://liveoption.example.com` in `.env`, replacing
   the example hostname with your own. Keep `.env` mode `0600`.
3. Run `uv run liveoption setup` and `uv run liveoption web --prepare-only`.
   The second command prepares the frontend without starting a server.
4. Copy [liveoption.service](systemd/liveoption.service) to
   `~/.config/systemd/user/`, adjusting its checkout path if necessary:

   ```bash
   mkdir -p ~/.config/systemd/user
   cp deploy/systemd/liveoption.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now liveoption.service
   journalctl --user -u liveoption.service -n 100 --no-pager
   ```

The service runs the local Web/API launcher: Web on `127.0.0.1:3001` and
the internal API on `127.0.0.1:19720`. QFinZero runs separately. Both Node.js
22+ and pnpm must be on the service's `PATH`; configure a systemd override
when using a per-user Node installation. User services need systemd lingering
to continue after logout (`sudo loginctl enable-linger "$USER"`).

## HTTPS proxy

Point your hostname at your server, allow inbound HTTP/HTTPS for Caddy, and
replace `liveoption.example.com` in [Caddyfile](caddy/Caddyfile). Validate it
with `caddy validate --config deploy/caddy/Caddyfile` before installing it in
your Caddy service. The template proxies to the loopback-only frontend and
streams SSE responses without proxy buffering. Keep the internal API and
QFinZero service ports off the public network.

Verify the application through the public hostname:

```bash
curl --fail https://liveoption.example.com/api/session
# Before sign-in: {"authenticated":false}
```

Sign in through the browser and run a short test. Keep provider credentials
in server-side API settings; never embed them in frontend build variables.

## Updates and backups

Wait for running tasks to finish before restarting this service; systemd
stops its child workers along with the launcher. Update the checkout, install
locked dependencies, rebuild with `uv run liveoption web --prepare-only`,
then run `systemctl --user restart liveoption.service`.

Back up `.env` and `.liveoption/` together while the application and its
workers are stopped. Store encrypted backups outside the repository and
restrict their permissions. Back up QFinZero's data and state separately
according to its own documentation. Rotating the Web access token revokes
existing browser sessions.

The optional [legacy result importer](backfill/README.md) can import older
batch output into a local database. It is unnecessary for a new installation.
