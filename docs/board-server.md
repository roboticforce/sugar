# Sugar board server: sharing one board with a team

Status: v5 (opt-in). Companion to [`local-dashboard.md`](./local-dashboard.md).

The dashboard is a client of one SQLite board: the Sugar work queue
(`--board sugar`, the default) or a Hermes Kanban board (`--board kanban`).
By default it is a single-user, loopback-only, unauthenticated server. This
document covers the opt-in way to expose the same server to a team — either on
a trusted LAN or behind a TLS-terminating reverse proxy — with scoped bearer
tokens.

## 1. Two deployment modes

| | default (v1 behaviour) | remote / team mode |
|---|---|---|
| CLI | `sugar dashboard` | `--bind ADDR --allow-remote --token-file PATH` |
| Bind | `127.0.0.1` only | explicit non-loopback address |
| Auth | none | scoped bearer token or `sugar_board` cookie |
| Origin guard | loopback origins only | loopback or same-host browser origin |
| Body cap / security headers | 1 MB / on | 1 MB / on |
| TLS | not needed (loopback) | terminated by a reverse proxy |

The default mode is unchanged: no token file, no auth headers, `127.0.0.1:7331`.
Local tools and agents (`sugar run`, MCP, curl on the same machine) keep working
exactly as before.

## 2. Enabling remote mode

```bash
# Preferred: bind loopback, expose it through a TLS reverse proxy (see §6).
# Tokens are enforced whenever they are configured, even on a loopback bind.
sugar dashboard --token-file ~/.sugar/board-tokens

# Trusted LAN, no proxy (token still travels in cleartext - use a VPN or TLS).
sugar dashboard --bind 0.0.0.0 --allow-remote --token-file ~/.sugar/board-tokens

# Specific interface / board source
sugar dashboard --bind 192.168.1.20 --allow-remote \
  --token-file ~/.sugar/board-tokens --board kanban
```

Rules, enforced before the server starts:

- A non-loopback `--bind` is refused unless `--allow-remote` is also passed.
- A non-loopback bind is also refused unless at least one token source is given
  (`--token-file` or `--token`).
- `--host` is still accepted as a deprecated alias for `--bind`; passing both is
  an error.
- `--bind` defaults to `127.0.0.1`.
- A bad token file (permissions, syntax, duplicates) exits 1 with a message and
  never starts the server.

When tokens are configured the server prints only token **names/scopes**, never
the secrets:

```
🍰 Sugar dashboard
   URL:      http://127.0.0.1:7331
   Auth:     2 token(s): alice=read, bob=write
```

## 3. Token file

One token per line, `name:scope:token`:

```text
# name : scope : secret       (blank lines and # comments are ignored)
alice:read:3f1c9a2d8e7b4a6f0c5d3e2b1a9f8e7d6c5b4a39281706f5e4d3c2b1a0f9e8d7
bob:write:9d8c7b6a5f4e3d2c1b0a9f8e7d6c5b4a39281706f5e4d3c2b1a0f9e8d7c6b5a4
```

Generate it with a restrictive umask:

```bash
umask 077
{
  echo "alice:read:$(openssl rand -hex 32)"
  echo "bob:write:$(openssl rand -hex 32)"
} > ~/.sugar/board-tokens
chmod 600 ~/.sugar/board-tokens
```

Rules:

- **mode 0600** (owner read/write). Files that are readable by group or others
  are refused on POSIX systems; on Windows the permission check is skipped.
- Token secrets must be non-empty printable ASCII and must not contain
  whitespace, commas, semicolons or quotes (they are also set as cookie values).
- Names must be unique (case-insensitive) and secrets must be unique.
- At least one token is required.
- Tokens are read once at startup. To rotate a token: edit the file and restart
  the server. There is no hot reload and no per-token expiry in v1.
- The token file is the only place a secret is persisted. The dashboard never
  logs tokens, writes them to the activity spine, or includes them in JSON.

`--token TOKEN` (single ad-hoc token, scope `write`) exists for quick testing.
It is visible in shell history and the process list, prints a warning, and is
not recommended for team use.

## 4. Scopes

| scope | allows |
|---|---|
| `read` | `GET`, `HEAD` on `/api/*` (including `/api/events` SSE and `/api/activity`) |
| `write` | everything a read token can do, plus `POST` / `PATCH` / `DELETE` |

`write` implies `read`.

Errors are JSON and never echo the supplied token:

| situation | status | body |
|---|---|---|
| missing / wrong token or cookie | `401` | `{"error":"unauthorized","detail":"missing or invalid board token"}` |
| valid `read` token used for a write | `403` | `{"error":"forbidden_scope","detail":"'read' token may not use POST"}` |
| write from a foreign browser origin | `403` | `{"error":"forbidden_origin",...}` |

`GET /api/health` is intentionally public (clients probe liveness). In token
mode it returns only `{ok, version}` — no absolute DB path, read-only flag or
uptime — because an unauthenticated endpoint should not disclose host details
to the network. Local (no-token) mode keeps the richer response for tooling.

## 5. Client authentication

Native tools can send a bearer header:

```bash
curl -s -H "Authorization: Bearer $BOARD_READ_TOKEN" http://127.0.0.1:7331/api/tasks
curl -s -X POST -H "Authorization: Bearer $BOARD_WRITE_TOKEN" \
     -H 'Content-Type: application/json' \
     -d '{"title":"from a teammate"}' http://127.0.0.1:7331/api/tasks
```

Browsers and any client that cannot set an `Authorization` header (including
`EventSource`) use the cookie flow:

```text
GET /?token=<token>   ->  303 Location: /
                          Set-Cookie: sugar_board=<token>; HttpOnly;
                          SameSite=Strict; Path=/; Max-Age=86400[; Secure]
```

- The token is validated first; an invalid `?token=` returns `401` and sets no
  cookie.
- The redirect drops the token from the address bar, so it is not in the
  browser history or a `Referer` header. The response is `no-store`.
- The cookie is the same secret with the same scope as the token. `Secure` is
  set only when the request scheme is `https` (typically via a TLS proxy); plain
  HTTP LAN mode cannot set a Secure cookie.
- `?token=` is accepted **only** on `/`. Query-string tokens on `/api/*` are
  ignored (`401`) so tokens do not end up in API access logs.
- Sign out by clearing the `sugar_board` cookie (or restart with the token
  removed).

## 6. TLS via a reverse proxy (recommended)

Bind loopback and let a proxy own TLS and the network boundary:

```bash
sugar dashboard --token-file ~/.sugar/board-tokens        # 127.0.0.1:7331
```

Caddy:

```caddyfile
board.example.com {
    reverse_proxy 127.0.0.1:7331
}
```

nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:7331;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Requirements:

- **Preserve `Host`.** Remote-mode the Origin guard accepts a write only when
  the browser `Origin` matches the request `Host`. Rewriting `Host` to
  `127.0.0.1:7331` makes remote browser writes fail with `403 forbidden_origin`.
- **Forward `X-Forwarded-Proto: https`.** uvicorn trusts proxy headers only from
  `127.0.0.1` (the server config sets `forwarded_allow_ips=127.0.0.1`); this is
  what makes the cookie `Secure`.
- Do not expose the uvicorn port directly; the proxy is the only listener.
- The initial `/?token=` request will still appear in the proxy's access log if
  you log query strings. Configure query redaction, or treat that token as
  one-time and rotate it after bootstrap.

Plain `--bind 0.0.0.0` without a proxy sends tokens, cookies and task text in
cleartext. Only use it on a trusted LAN or inside a VPN.

## 7. Threat model

What remote mode with tokens protects against:

- Unauthenticated network access to the board API (401 before any handler).
- Accidentally exposing a board by binding a non-loopback address without
  authentication (both `--allow-remote` and a token source are required).
- Over-privileged clients: read tokens cannot mutate the board.
- Cross-site request forgery from a web page: SameSite=Strict cookie plus the
  Origin same-host gate.
- Token leakage through logs/shoulders: secrets are never logged, echoed in
  errors or activity events; the token is removed from the URL after bootstrap.
- Token guessing: high-entropy tokens, constant-time comparison, no plaintext
  storage outside the 0600 file.

What is **not** provided (v1):

- TLS termination or certificate management — use a reverse proxy.
- SSO / OIDC / per-user accounts. Each bearer token is a shared credential.
- Per-user identity or audit. Token names are not propagated as the activity
  actor; the actor comes from the task payload / `X-Sugar-Actor` headers.
  Per-task ACLs do not exist: any valid token can read the whole board.
- Rate limiting, lockout, intrusion detection or DoS protection.
- Token expiry or hot rotation; rotate by editing the file and restarting.
- Encryption at rest (the board is the same SQLite database as before).

## 8. Operations checklist

- `chmod 600` the token file; never commit it or place it in a synced folder.
- Use one token per person/tool, `read` for watchers and `write` only for
  producers.
- Rotate a token when someone leaves or when a `--token` value may have leaked.
- Keep the uvicorn port off the LAN when behind a proxy.
- Verify from another machine:
  ```bash
  curl -s http://BOARD:7331/api/health            # 200, no auth
  curl -s -o /dev/null -w '%{http_code}\n' http://BOARD:7331/api/tasks   # 401
  curl -s -H "Authorization: Bearer $READ" http://BOARD:7331/api/tasks  # 200
  curl -s -X POST -H "Authorization: Bearer $READ" \
       -H 'Content-Type: application/json' -d '{"title":"x"}' \
       http://BOARD:7331/api/tasks                 # 403 forbidden_scope
  ```
- The CLI smoke test in `docs/local-dashboard.md` §6 still applies; add
  `-H "Authorization: Bearer …"` when tokens are enabled.
