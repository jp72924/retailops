# RetailOps Agent Runtime Integration

This connects an external agent runtime -- a chatbot like PicoClaw -- to
RetailOps, so it can read and write real data (orders, stock, customers)
through a controlled account. Tested with PicoClaw v0.3.1 on Linux and
Windows.

This document has two parts. **Quick Start** is enough to install PicoClaw
and connect it -- no prior technical knowledge assumed. **Full Reference**
goes underneath it: the reasoning behind each choice, how to evaluate or add
another agent runtime, and deeper troubleshooting -- for whoever configures,
extends, or debugs this integration.

---

## Quick Start

### Before you start

- RetailOps installed and running, reachable from wherever the agent will
  run (see `INSTALL.md`).
- Decide the RetailOps role for the agent: `Staff` (reads data, creates
  routine records) unless it needs to run the order lifecycle or adjust
  stock, in which case `Manager`. Never `Admin`, and never point it at your
  own account — create a dedicated one (next step covers this).
- On Linux, a dedicated system user for the agent. Keeps its access separate
  from yours and from RetailOps' own process.

### Install PicoClaw

`<agent-user>` throughout this guide is whichever account runs PicoClaw —
your own account for local development, or a dedicated one for production.

Download the release, verify it, and extract it — works under your own
account, nothing else to set up first:

```bash
curl -LO https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_Linux_x86_64.tar.gz
curl -LO https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_0.3.1_checksums.txt
sha256sum -c --ignore-missing picoclaw_0.3.1_checksums.txt
mkdir -p ~/picoclaw/bin
tar xzf picoclaw_Linux_x86_64.tar.gz -C ~/picoclaw/bin
~/picoclaw/bin/picoclaw version
```

In production, isolate the agent under its own account instead, then run
the same commands as that user:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin <agent-user>
```

On Windows:

```powershell
Invoke-WebRequest -Uri "https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_Windows_x86_64.zip" -OutFile "picoclaw_Windows_x86_64.zip"
Invoke-WebRequest -Uri "https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_0.3.1_checksums.txt" -OutFile "picoclaw_0.3.1_checksums.txt"
(Get-FileHash .\picoclaw_Windows_x86_64.zip -Algorithm SHA256).Hash
Expand-Archive .\picoclaw_Windows_x86_64.zip -DestinationPath C:\Users\<user>\picoclaw\bin
& C:\Users\<user>\picoclaw\bin\picoclaw.exe version
```

`picoclaw version` should print `0.3.1`.

### Get a RetailOps token

```bash
mkdir -p ~/.picoclaw
cd /path/to/retailops
.venv/bin/python manage.py shell -c "from core.models import User; from rest_framework.authtoken.models import Token; t,_=Token.objects.get_or_create(user=User.objects.get(email='agent-service@example.com')); open('/home/<agent-user>/.picoclaw/retailops.env','w').write('RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1\nRETAILOPS_API_TOKEN='+t.key+'\nRETAILOPS_TIMEOUT=30\nPYTHONPATH=/path/to/retailops\n'); print('token prefix', t.key[:6])"
chmod 600 /home/<agent-user>/.picoclaw/retailops.env
```

Use the email of the dedicated account from "Before you start" — it must
already exist with the role you chose.

On Windows:

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py shell -c "from core.models import User; from rest_framework.authtoken.models import Token; t,_=Token.objects.get_or_create(user=User.objects.get(email='agent-service@example.com')); open(r'C:\Users\<user>\.picoclaw\retailops.env','w').write('RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1\nRETAILOPS_API_TOKEN='+t.key+'\nRETAILOPS_TIMEOUT=30\nPYTHONPATH=C:\\path\\to\\retailops\n'); print('token prefix', t.key[:6])"
```

### Configure PicoClaw

Save as `~/.picoclaw/config.json` (Windows: `C:\Users\<user>\.picoclaw\config.json`,
with Windows-style paths inside it):

```json
{
  "version": 3,
  "agents": {
    "defaults": {
      "workspace": "/home/<agent-user>/.picoclaw/workspace",
      "restrict_to_workspace": true,
      "model_name": "glm-5.3-flash",
      "max_tokens": 8192,
      "context_window": 200000,
      "max_tool_iterations": 20
    }
  },
  "model_list": [
    {
      "model_name": "glm-5.3-flash",
      "provider": "openrouter",
      "model": "z-ai/glm-5.3-flash",
      "api_base": "https://openrouter.ai/api/v1"
    }
  ],
  "channel_list": {
    "telegram": {
      "enabled": true,
      "type": "telegram",
      "allow_from": ["<your-telegram-user-id>"],
      "settings": { "use_markdown_v2": false }
    }
  },
  "tools": {
    "mcp": { "enabled": true, "servers": {} },
    "exec": { "enabled": false }
  },
  "heartbeat": { "enabled": false, "interval": 30 },
  "gateway": { "host": "localhost", "port": 18790, "log_level": "info" }
}
```

Save as `~/.picoclaw/.security.yml`, then restrict it:

```yaml
model_list:
  glm-5.3-flash:
    api_keys:
      - "<provider-api-key>"
channels:
  telegram:
    token: "<telegram-bot-token>"
channel_list:
  telegram:
    settings:
      token: "<telegram-bot-token>"
```

```bash
chmod 600 ~/.picoclaw/.security.yml
```

On Windows: `icacls "C:\Users\<user>\.picoclaw\.security.yml" /inheritance:r /grant:r "$($env:USERNAME):(R,W)"`.

Two things to know before moving on:

- Secrets are matched by `model_name`, not by the provider or model name — if
  `picoclaw status` later shows your provider as "not set", the spelling
  doesn't match exactly.
- The next step (`picoclaw mcp add`) can rewrite this file and move the
  `channels:` block into `channel_list.telegram.settings`, dropping the
  token in the process. Writing the token in both places, as above, survives
  either shape — just re-check this file after running `mcp add`.

### Connect PicoClaw to RetailOps

```bash
picoclaw mcp add retailops \
  --env-file "/home/<agent-user>/.picoclaw/retailops.env" \
  --no-deferred \
  -- "/path/to/retailops/.venv/bin/python" -m mcp_server.server
```

On Windows:

```bash
picoclaw mcp add retailops \
  --env-file "C:\Users\<user>\.picoclaw\retailops.env" \
  --no-deferred \
  -- "C:\path\to\retailops\.venv\Scripts\python.exe" -m mcp_server.server
```

The `PYTHONPATH` line from the token step is required — PicoClaw has no way
to set a working directory for this process, and without it the connection
fails with a missing-module error.

### Connect a chat channel (Telegram)

1. Message `@BotFather` on Telegram, send `/newbot`, follow the prompts. It
   gives you a bot token — put it in `.security.yml` above.
2. Message `@userinfobot` to get your own numeric Telegram ID — put it in
   `config.json`'s `allow_from` above.
3. Leaving `allow_from` empty lets anyone who finds the bot use it with your
   RetailOps token. Don't skip this.

### Run it

Two long-running processes, in separate terminals:

```bash
cd /path/to/retailops && .venv/bin/python manage.py runserver
```

```bash
picoclaw gateway
```

On Windows, the same two commands, in separate terminals:

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py runserver
```

```bash
picoclaw gateway
```

In production, run PicoClaw under a supervisor instead of a bare terminal:

```ini
[Unit]
Description=PicoClaw agent runtime (RetailOps integration)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<agent-user>
Group=<agent-user>
WorkingDirectory=/home/<agent-user>
ExecStart=/home/<agent-user>/picoclaw/bin/picoclaw gateway
Restart=on-failure
RestartSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/<agent-user>/.picoclaw
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
```

Save as `/etc/systemd/system/picoclaw.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now picoclaw.service
sudo systemctl status picoclaw.service
```

RetailOps itself keeps running however you already run it in production too
(see `INSTALL.md`).

### Confirm it works

```bash
picoclaw mcp test retailops
```

Expected:

```
Connected to MCP server protocol=2025-11-25 server=retailops serverName=RetailOps serverVersion=1.28.1
Listed tools from MCP server server=retailops toolCount=59
✓ MCP server "retailops" reachable (59 tools).
```

Then, in the chat channel, ask something real — "which products are low on
stock?" If you get a real answer back, it's connected end to end.

### Common problems

- `picoclaw status` shows the provider as "not set" even though the key is
  in `.security.yml`: the key's name doesn't match `model_name` exactly.
- `config.json contains unknown field(s): build_info`: leftover config from
  an older PicoClaw version — back up `.security.yml`, write a fresh
  `config.json`.
- `ModuleNotFoundError: No module named 'mcp_server'`: `PYTHONPATH` is
  missing from the env file.
- `failed to connect: calling "initialize": EOF`, only when running
  `picoclaw` by hand instead of as a service: the shell's current directory
  isn't readable by the agent's system user. `cd` to that user's home first,
  or just run it as the `systemd` service instead — it doesn't have this
  problem.
- A tool call returns a plain `400` page with no detail, on a server that
  also serves other sites behind a reverse proxy: the agent is talking to
  the backend directly instead of through the proxy. Point
  `RETAILOPS_BASE_URL` at the proxied `https://` address instead of
  `127.0.0.1`.
- A tool call returns `301` in that same setup: same cause, same fix as
  above.
- Tool calls return `401`: the token is missing or was revoked — check the
  env file, or repeat the token step.
- Tool calls return `403`: the account's role doesn't allow that action —
  see "Before you start".
- The bot answers people other than you: `allow_from` in `config.json` is
  empty.
- The Telegram bot stops responding after running `picoclaw mcp add` again:
  it rewrote `.security.yml`. Re-add the token under both shapes, as noted
  in "Configure PicoClaw".

### Other runtimes

OpenClaw is validated and documented in Full Reference §10 — it doesn't
have its own Quick Start yet, so follow §10 directly. Hermes Agent isn't
documented yet; once it is, it'll follow this same structure.

---

## Full Reference

Everything below is for engineers extending this integration, adding a new
runtime, or debugging past what Quick Start's troubleshooting covers. You do
not need to repeat Quick Start -- this is additional depth, not a second
walkthrough.

### Table of Contents

1. [What This Covers](#1-what-this-covers)
2. [Agent Runtime Requirements](#2-agent-runtime-requirements)
3. [Choosing a Transport](#3-choosing-a-transport)
4. [Identity and Least Privilege](#4-identity-and-least-privilege)
5. [Provisioning a Token for an Agent](#5-provisioning-a-token-for-an-agent)
6. [Tool Surface Sizing](#6-tool-surface-sizing)
7. [Verification Ladder](#7-verification-ladder)
8. [Security Checklist Before Going Live](#8-security-checklist-before-going-live)
9. [Integration: PicoClaw](#9-integration-picoclaw)
10. [Integration: OpenClaw](#10-integration-openclaw)
11. [Integration: Hermes Agent](#11-integration-hermes-agent)
12. [Cross-Runtime Troubleshooting](#12-cross-runtime-troubleshooting)

---

### 1. What This Covers

`MCP_GUIDE.md` documents the RetailOps MCP layer itself — its architecture, tool
catalog, transports, request lifecycle, error contract, and security model. Its
integration guides (§10–§14) target *MCP clients*: Claude Desktop, a raw SSE or
streamable-HTTP connection, a custom Python client, LangChain.

This document covers a different class of consumer: the **agent runtime**. An
agent runtime is a long-running process with its own configuration schema,
secret store, model routing, chat channels, scheduler, and — usually — a shell
execution tool. It connects to RetailOps as an MCP client, but the integration
work is mostly outside the MCP protocol: deciding which RetailOps identity it
holds, how its secrets are stored, how 59 tools fit in its model's context, and
what its own attack surface adds to yours.

PicoClaw is the first such runtime validated against RetailOps. OpenClaw and
Hermes Agent are planned. Sections 2–8 apply to all of them; sections 9–11 are
per-runtime.

**Not repeated here** — read these in `MCP_GUIDE.md` instead:

| Topic | Where |
|---|---|
| Transport mechanics (stdio, SSE, streamable-HTTP) | `MCP_GUIDE.md` §8 |
| Remote HTTP deployment, reverse proxies, TLS | `MCP_GUIDE.md` §12, §16 |
| Full environment variable reference | `MCP_GUIDE.md` §15 |
| Effective identity and the token verification flow | `MCP_GUIDE.md` §16 |
| Error contract and error codes | `MCP_GUIDE.md` §17 |
| Tool, resource, and prompt catalogs | `MCP_GUIDE.md` §5–§7 |

---

### 2. Agent Runtime Requirements

Assess a candidate runtime against this checklist before starting integration
work. A runtime that fails a "Required" row cannot connect to RetailOps without
changes to the runtime itself.

| Capability | Required? | Why RetailOps needs it |
|---|---|---|
| MCP client support | Required | RetailOps exposes tools only over MCP. A runtime with a generic "HTTP tool" feature is not equivalent — it bypasses the schema contract and the error handling in `api/exceptions.py`. |
| stdio transport | Required for local | The MCP server runs as a child process of the agent. This is the simplest deployment and needs no TLS or token verification hop. |
| streamable-HTTP or SSE transport | Required for remote | Needed when the agent and the RetailOps backend are on different hosts. |
| Per-server environment injection (`env` map or env file) | Required for stdio | The server process reads `RETAILOPS_BASE_URL` and `RETAILOPS_API_TOKEN` from its environment. Without injection there is no way to give the child process a token. |
| Per-server HTTP header injection | Required for remote | `MCP_AUTH_MODE=retailops-token` expects `Authorization: Bearer <RetailOps token>` on every request. |
| Working directory (`cwd`) per server | Preferred | `python -m mcp_server.server` resolves the package from the working directory. If the runtime cannot set `cwd`, set `PYTHONPATH` to the repo root through the environment instead — see §9 Step 5. That alone is not always sufficient: the `mcp` library does its own independent `.env` lookup relative to the process's real `cwd`, so an inherited, unreadable `cwd` can still break the connection with a `PermissionError`. See §9's "Linux-specific gotchas" (Gotcha 1) for the reproduced trace and fix. |
| Lazy or deferred tool loading | Preferred | RetailOps exposes 59 tools. Runtimes that always inline every tool schema spend meaningful context and cost per turn. See §6. |
| Secret storage separate from main config | Preferred | Keeps the RetailOps token out of the file you would otherwise share or commit. |
| Per-user or per-channel allowlisting | Preferred | An agent reachable from a public chat channel is reachable by anyone who finds it, and it holds your RetailOps token. |

Record the result for each new runtime in its own section, so the next
integration starts from a known answer rather than a fresh investigation.

---

### 3. Choosing a Transport

| Situation | Transport | RetailOps configuration |
|---|---|---|
| Agent runs on the same host as the backend | `stdio` | None. The agent spawns `python -m mcp_server.server`; defaults apply (`MCP_AUTH_MODE=local`). |
| Agent runs on the same host, but you want one shared server for several clients | `sse` or `streamable-http` on loopback | `MCP_TRANSPORT=streamable-http`, default `MCP_HOST=127.0.0.1`. |
| Agent runs on a different host (edge device, VPS, container) | `streamable-http` | `MCP_AUTH_MODE=retailops-token`, `MCP_PUBLIC_BASE_URL=https://...`, `MCP_ALLOWED_HOSTS=<host>`, behind a TLS reverse proxy. |

Prefer `stdio` whenever the agent and backend share a host. It removes the
network surface entirely: no TLS to terminate, no DNS rebinding protection to
configure, no bearer token in flight, and the MCP process lives and dies with
the agent.

The remote path is hard-gated in `mcp_server/server.py`. Binding an HTTP
transport to a non-loopback host without `MCP_AUTH_MODE=retailops-token` raises
at startup, as does a missing `MCP_ALLOWED_HOSTS` or a non-HTTPS
`MCP_PUBLIC_BASE_URL`. This is deliberate — see `MCP_GUIDE.md` §16.

> **Note:** Under `stdio` the MCP server holds one identity for the whole
> process, taken from `RETAILOPS_API_TOKEN`. Under the HTTP transports each
> client supplies its own token per request. An agent runtime is a
> single-identity consumer either way, so this rarely changes the design — but
> it does mean a stdio agent cannot switch RetailOps users mid-session.

---

### 4. Identity and Least Privilege

**This is the section that matters most.** Everything else is plumbing.

The MCP layer applies no per-tool access control. It authenticates to the REST
API as one RetailOps user and forwards `Authorization: Token <token>`; the API
remains the sole authority for permissions. Whatever that user's `Role` permits,
the agent can do — all 59 tools are offered to the model, and each one succeeds
or fails purely on the backing user's role.

Practical consequences:

- **You cannot grant an agent "read-only access to orders."** There is no
  narrower grant than the role itself. Choose the role, not the tool list.
- **Never point an agent at a human's account.** Tool calls are indistinguishable
  from that person's own API activity in any audit trail.
- **Create one dedicated service user per agent runtime.** If two agents share a
  token, revoking one revokes both.

Role selection:

| Role | Give it to an agent when | Notable exclusions |
|---|---|---|
| `Staff` | The agent answers questions — stock levels, order status, customer lookups — and creates routine records. Correct default for assistants. | User administration, system settings, most destructive operations. |
| `Manager` | The agent drives operations: order lifecycle transitions, inventory adjustments, product and category maintenance. | User administration and role assignment. |
| `Admin` | Effectively never. An LLM-driven process with Admin can modify users, roles, and system settings. | — |
| `Kiosk` | Never for an agent. This role exists for `KioskStation` API-key auth on the `/api/v1/kiosk/` namespace and is not reachable through the MCP tools. | — |

Related tools in `mcp_server/tools/auth.py`:

- `retailops_whoami` — confirms which identity is actually in effect and where
  the token came from. Run it first when debugging permission errors.
- `retailops_login` — obtains a token from the public `/auth/token/` endpoint.
  It activates that token process-wide **only under stdio**; on HTTP transports
  it returns the token and changes nothing, because identity there is per
  request.
- `retailops_logout` — revokes the effective token. It refuses to revoke a token
  supplied through `RETAILOPS_API_TOKEN` unless you pass
  `revoke_env_token=True`, since that would break the process until the
  environment is updated.

To revoke an agent's access immediately, call `POST /api/v1/auth/token/revoke/`
as that user, or delete the token row. The agent's next tool call fails with an
authentication error; nothing else is affected.

---

### 5. Provisioning a Token for an Agent

`MCP_GUIDE.md` §15 covers the two standard ways to obtain a token (Django shell,
or `POST /auth/token/`). For an agent runtime, prefer writing the token straight
into a restricted file that the runtime reads at process start, rather than
pasting it into the runtime's main configuration file — main configs get shared,
copied between machines, and occasionally committed.

The command below mints a token for a chosen account and writes a complete env
file in one step. Adjust the account, repo path, and output path.

```bash
cd /path/to/retailops
.venv/bin/python manage.py shell -c "from core.models import User; from rest_framework.authtoken.models import Token; t,_=Token.objects.get_or_create(user=User.objects.get(email='agent-service@example.com')); open('/etc/retailops/agent.env','w').write('RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1\nRETAILOPS_API_TOKEN='+t.key+'\nRETAILOPS_TIMEOUT=30\n'); print('token prefix', t.key[:6])"
chmod 600 /etc/retailops/agent.env
```

**Windows equivalent** (cmd.exe — note the backslash paths, `&&` chaining, and
the absence of `%`, which cmd.exe would try to expand):

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py shell -c "from core.models import User; from rest_framework.authtoken.models import Token; t,_=Token.objects.get_or_create(user=User.objects.get(email='agent-service@example.com')); open(r'C:\path\to\agent.env','w').write('RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1\nRETAILOPS_API_TOKEN='+t.key+'\nRETAILOPS_TIMEOUT=30\n'); print('token prefix', t.key[:6])"
```

Restrict the file to your account:

```powershell
icacls "C:\path\to\agent.env" /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
```

The resulting file:

```env
RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1
RETAILOPS_API_TOKEN=<40-char DRF token>
RETAILOPS_TIMEOUT=30
```

`Token.objects.get_or_create` is idempotent — re-running it returns the existing
token rather than rotating it. To rotate, delete the token row first, then
re-run; every client holding the old value stops working immediately.

---

### 6. Tool Surface Sizing

RetailOps registers **59 tools across 12 domains**:

| Domain | Tools | Domain | Tools |
|---|---|---|---|
| Orders | 15 | Categories | 5 |
| Users | 7 | Recipient Profiles | 5 |
| Products | 6 | Inventory | 4 |
| Customers | 5 | Payments | 4 |
| Auth | 3 | Roles | 2 |
| Settings | 2 | Dashboard | 1 |

Every tool's JSON schema is sent to the model on every turn unless the runtime
supports deferred loading. Two strategies:

**Always visible.** Simplest, and correct when the agent's whole purpose is
RetailOps. Every tool is immediately callable with no discovery step. Cost is a
fixed per-turn overhead — negligible on a cheap high-context model, noticeable
on an expensive one, and potentially degrading on a model with a small context
window or weak tool selection.

**Deferred / lazy discovery.** The runtime hides the tools behind a search tool
and unlocks matches on demand. Correct when the agent connects to several MCP
servers, or runs on a small model. Costs an extra turn on first use of a domain
and depends on the search actually surfacing the right tool.

Recommendation: start always-visible while validating the integration, so a
failure is unambiguously a tool or permission problem rather than a discovery
miss. Switch to deferred once the agent has other MCP servers attached, or if
per-turn cost matters.

> **Note:** `GET /api/v1/mcp-skill/` returns a self-describing capability card
> for the whole MCP surface (see `MCP_GUIDE.md` §19 and `API_GUIDE.md` §4.13).
> It is useful for seeding an agent's system prompt with what RetailOps can do,
> without inlining 59 schemas.

---

### 7. Verification Ladder

Test in this order. Each rung proves something the next one depends on, so the
first failure localizes the problem instead of leaving you guessing.

| # | Test | Proves | Typical failure |
|---|---|---|---|
| 1 | Runtime's MCP connectivity probe | The runtime can spawn or reach the server, and the MCP handshake completes | Wrong interpreter path, missing `PYTHONPATH`, unreachable URL |
| 2 | Tool listing returns 59 tools | Registration succeeded; the tool schemas are visible to the model | Partial registration, stale server version |
| 3 | A read-only tool call (`retailops_whoami`, then `retailops_get_dashboard`) | The token is valid, the API is reachable, and the identity is the one you intended | Connection refused (API not running), 401 (bad or revoked token) |
| 4 | A domain read (`retailops_list_products`, low-stock check) | Role permissions allow reads on real business data | 403 from a too-narrow role |
| 5 | A write (create a customer, then a sales order) | Role permissions allow writes; order numbering via `SequenceCounter` works under the agent's identity | 403, or a validation error surfaced through `api.exceptions` |
| 6 | A scheduled or channel-pushed task | The runtime's scheduler and chat channel work end to end on top of the MCP layer | Runtime-specific; not a RetailOps failure |

Rung 2 has a subtlety worth internalizing: **listing tools never touches the
REST API.** A connectivity probe can report full success while the Django
server is stopped. The first thing that actually requires the backend is a tool
*call* — rung 3. Do not treat a green probe as proof the integration works.

---

### 8. Security Checklist Before Going Live

Work through this before pointing an agent at anything other than a local
throwaway database.

- [ ] **Dedicated service user**, not a human account, not a demo account.
- [ ] **Narrowest workable role** — `Staff` unless the agent genuinely needs to
      transition orders or adjust stock (§4).
- [ ] **Token in a restricted file** the runtime reads at startup, not in a
      config file that gets shared or copied. `chmod 600` / `icacls` applied.
- [ ] **Revocation path tested** — you have confirmed that revoking the token
      actually stops the agent, before you need it to.
- [ ] **Chat channels allowlisted** to specific user IDs. An open channel means
      anyone who finds the bot inherits your RetailOps token through it.
- [ ] **The runtime's own tools reviewed.** Most agent runtimes ship a shell
      execution tool, file read/write, and a scheduler. These are usually
      enabled by default and are a larger risk than the RetailOps tools: shell
      access on the host generally beats API access to the backend. Disable what
      the agent does not need.
- [ ] **Throttle headroom checked** against the agent's expected loop rate:

      | Scope | Rate | Applies to |
      |---|---|---|
      | `user` | 600/min | Global ceiling, all authenticated endpoints |
      | `order_transition` | 60/min | submit / confirm / ship / deliver / cancel / refund |
      | `inventory_adjust` | 30/min | Manual stock adjustments |
      | `ocr_verify` | 12/min | Receipt OCR verification |
      | `login` | 20/min | `POST /auth/token/`, IP-based |

- [ ] **Remote transport hardened** if not using stdio: HTTPS, `MCP_ALLOWED_HOSTS`
      set, `MCP_AUTH_MODE=retailops-token` (`MCP_GUIDE.md` §16).
- [ ] **Upstream maturity assessed.** Agent runtimes in this space move fast and
      are frequently pre-1.0 with explicit "not for production" notices from
      their own maintainers. Read the upstream project's current security
      posture and honor it — a pre-1.0 runtime holding a write-capable token
      against production data is a decision, not an accident.

---

### 9. Integration: PicoClaw

[PicoClaw](https://github.com/sipeed/picoclaw) is an ultra-lightweight agent
runtime written in Go by Sipeed. It runs on very small hardware, supports many
chat channels (Telegram, Discord, Matrix, Slack, IRC, and others), has a cron
scheduler, and includes a native MCP client.

**Validated configuration — Linux**

| Item | Value |
|---|---|
| PicoClaw version | v0.3.1 |
| Platform | Linux x86_64, systemd-based distribution (binaries also published for Windows, macOS, FreeBSD, Android, and ARM/RISC-V/MIPS/LoongArch) |
| Minimum hardware | 1 shared vCPU / 1GB RAM — no memory pressure observed running alongside the RetailOps dev server |
| Deployment | `systemd` service, dedicated system user (production); also validated running directly under the invoking user's own account, no service, no dedicated user (development) |
| Transport | stdio |
| Result | 59 tools, MCP protocol `2025-11-25`, server `RetailOps 1.28.1` |
| Validated | 2026-09-10 |

**Validated configuration — Windows**

| Item | Value |
|---|---|
| PicoClaw version | v0.3.1 |
| Platform | Windows x86_64 |
| Deployment | Manual processes in separate terminals, single Windows user account |
| Transport | stdio |
| Result | 59 tools, MCP protocol `2025-11-25`, server `RetailOps 1.30.0` |
| Validated | 2026-09-10 |

The reported server version tracks the installed `mcp` package, so it moves with
`requirements.txt` — `1.28.1` in the Linux run and `1.30.0` in the Windows run
are both valid; RetailOps pins `mcp>=1.27.0,<2` because `mcp_server/` targets
the v1 API — see the troubleshooting table below. Neither number is a
deliberate target: each run's `mcp` package version simply reflects whenever
that environment's virtual environment was last installed or updated, and any
version satisfying the pin behaves identically for this integration. New
setups should install the latest version inside that range rather than aim
for either figure above.

Steps below default to the Linux layout, with the original Windows steps
included right after each one. On macOS, the Linux steps apply with the
platform tarball substituted — the configuration itself is identical.

#### Step 1: Install PicoClaw

Download the release asset for your platform along with the checksums file,
and verify before extracting — this works under your own account, nothing
else to set up first:

```bash
curl -LO https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_Linux_x86_64.tar.gz
curl -LO https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_0.3.1_checksums.txt
sha256sum -c --ignore-missing picoclaw_0.3.1_checksums.txt
```

Extract and confirm the binary runs:

```bash
mkdir -p ~/picoclaw/bin
tar xzf picoclaw_Linux_x86_64.tar.gz -C ~/picoclaw/bin
~/picoclaw/bin/picoclaw version
```

The archive contains `picoclaw` (CLI and gateway) and `picoclaw-launcher`
(optional web dashboard).

In production, isolate PicoClaw under a dedicated account instead of
running it as yourself — this is what exposed the findings in
"Linux-specific gotchas" below, which a single-account setup never
exercises. Create the account, then run the same commands above as that
user:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin <agent-user>
```

**On Windows:**

```powershell
Invoke-WebRequest -Uri "https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_Windows_x86_64.zip" -OutFile "picoclaw_Windows_x86_64.zip"
Invoke-WebRequest -Uri "https://github.com/sipeed/picoclaw/releases/download/v0.3.1/picoclaw_0.3.1_checksums.txt" -OutFile "picoclaw_0.3.1_checksums.txt"
```

Verification step:

```powershell
(Get-FileHash .\picoclaw_Windows_x86_64.zip -Algorithm SHA256).Hash
Select-String -Path .\picoclaw_0.3.1_checksums.txt -Pattern 'Windows_x86_64'
```

Extract and confirm the binary runs:

```powershell
Expand-Archive .\picoclaw_Windows_x86_64.zip -DestinationPath C:\Users\<user>\picoclaw\bin
& C:\Users\<user>\picoclaw\bin\picoclaw.exe version
```

The archive contains `picoclaw.exe` (CLI and gateway) and
`picoclaw-launcher.exe` (optional web dashboard).

> **Note:** If `~/.picoclaw/` already exists from an older PicoClaw version, back
> up `config.json` and `.security.yml` before continuing. Configs written by
> earlier versions can fail to migrate — the symptom is
> `config.json contains unknown field(s): build_info` on any command. Writing a
> fresh v3 config resolves it; the existing `workspace/` directory can be kept
> as is.

#### Step 2: Configure the runtime

PicoClaw reads `~/.picoclaw/config.json` (override with `PICOCLAW_CONFIG`; move
the whole data root with `PICOCLAW_HOME`). Version 3 of the schema is what
v0.3.1 expects. A minimal configuration covering models, one chat channel, and
MCP:

```json
{
  "version": 3,
  "agents": {
    "defaults": {
      "workspace": "/home/<agent-user>/.picoclaw/workspace",
      "restrict_to_workspace": true,
      "model_name": "glm-5.3-flash",
      "max_tokens": 8192,
      "context_window": 200000,
      "max_tool_iterations": 20
    }
  },
  "model_list": [
    {
      "model_name": "glm-5.3-flash",
      "provider": "openrouter",
      "model": "z-ai/glm-5.3-flash",
      "api_base": "https://openrouter.ai/api/v1"
    }
  ],
  "channel_list": {
    "telegram": {
      "enabled": true,
      "type": "telegram",
      "allow_from": ["<your-telegram-user-id>"],
      "settings": { "use_markdown_v2": false }
    }
  },
  "tools": {
    "mcp": { "enabled": true, "servers": {} },
    "exec": { "enabled": true, "enable_deny_patterns": true }
  },
  "heartbeat": { "enabled": false, "interval": 30 },
  "gateway": { "host": "localhost", "port": 18790, "log_level": "info" }
}
```

**On Windows**, the structure is identical; only `agents.defaults.workspace`
differs:

```json
"workspace": "C:\\Users\\<user>\\.picoclaw\\workspace"
```

Points worth understanding rather than copying blindly:

| Field | Why it is set this way |
|---|---|
| `model_list[].provider` + `model` | Use the explicit two-field form. With `provider` omitted, PicoClaw treats the first `/` in `model` as the provider name — which mangles OpenRouter ids like `z-ai/glm-5.3-flash`, since those contain their own slash. |
| `agents.defaults.model_name` | Must match a `model_name` in `model_list`, and also keys the secret lookup in Step 3. |
| `channel_list.telegram.allow_from` | An empty array means **anyone** who finds the bot can drive it. Set your numeric Telegram user ID (obtainable from `@userinfobot`). |
| `channel_list.telegram.settings.token` | Omit it here; supply it via `.security.yml` (Step 3). |
| `heartbeat.enabled` | When true, the agent wakes on a timer and consumes model tokens while idle. Leave it off until you want that behavior. |
| `tools.exec` | PicoClaw's shell execution tool. Enabled by default and reachable from chat channels — review §8 before exposing the bot. |

#### Step 3: Store secrets in `.security.yml`

PicoClaw maps secrets from `~/.picoclaw/.security.yml` onto config fields
automatically, so no keys need to live in `config.json`:

```yaml
model_list:
  glm-5.3-flash:
    api_keys:
      - "<provider-api-key>"
channels:
  telegram:
    token: "<telegram-bot-token>"
```

> **Note:** Secrets are keyed by **`model_name`**, not by provider or model id.
> Renaming a model in `config.json` without renaming its key here fails
> silently — the model simply has no key. `picoclaw status` reporting
> `OpenRouter API: not set` while a key is clearly present in the file is
> almost always this mismatch.

> **Note:** `picoclaw mcp add` rewrites `.security.yml` as well as `config.json`,
> and in v0.3.1 the rewrite can migrate the documented `channels:` block to a
> `channel_list.<name>.settings` shape — dropping channel tokens in the process.
> Model keys may also gain a `:0` suffix (`glm-5.3-flash:0`), which still
> resolves. Register the MCP server (Step 5) **before** writing channel secrets,
> re-read this file after any `picoclaw mcp add` / `remove` / `edit`, and
> consider writing channel tokens under both shapes so either normalization
> survives:
>
> ```yaml
> channels:
>   telegram:
>     token: "<telegram-bot-token>"
> channel_list:
>   telegram:
>     settings:
>       token: "<telegram-bot-token>"
> ```

Restrict the file, then confirm the mapping took effect:

```bash
chmod 600 ~/.picoclaw/.security.yml
~/picoclaw/bin/picoclaw status
```

**On Windows:**

```powershell
icacls "C:\Users\<user>\.picoclaw\.security.yml" /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
& C:\Users\<user>\picoclaw\bin\picoclaw.exe status
```

Expected: `Config: ... ✓`, `Workspace: ... ✓`, the model name you set, and a
`✓` next to your provider.

#### Step 4: Provision the RetailOps token

Follow §5, writing the env file where PicoClaw will read it. This example uses
the demo `manager@retailops.local` account against a local database; for
anything beyond local testing, create a dedicated service user first (§4).

```bash
cd /path/to/retailops
.venv/bin/python manage.py shell -c "from core.models import User; from rest_framework.authtoken.models import Token; t,_=Token.objects.get_or_create(user=User.objects.get(email='manager@retailops.local')); open('/home/<agent-user>/.picoclaw/retailops.env','w').write('RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1\nRETAILOPS_API_TOKEN='+t.key+'\nRETAILOPS_TIMEOUT=30\nPYTHONPATH=/path/to/retailops\n'); print('token prefix', t.key[:6])"
chmod 600 /home/<agent-user>/.picoclaw/retailops.env
```

**On Windows:**

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py shell -c "from core.models import User; from rest_framework.authtoken.models import Token; t,_=Token.objects.get_or_create(user=User.objects.get(email='manager@retailops.local')); open(r'C:\Users\<user>\.picoclaw\retailops.env','w').write('RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1\nRETAILOPS_API_TOKEN='+t.key+'\nRETAILOPS_TIMEOUT=30\nPYTHONPATH=C:\\path\\to\\retailops\n'); print('token prefix', t.key[:6])"
```

The `PYTHONPATH` line is not optional — see Step 5.

#### Step 5: Register the RetailOps MCP server

```bash
picoclaw mcp add retailops \
  --env-file "/home/<agent-user>/.picoclaw/retailops.env" \
  --no-deferred \
  -- "/path/to/retailops/.venv/bin/python" -m mcp_server.server
```

This writes the following into `config.json` under `tools.mcp.servers`:

```json
{
  "retailops": {
    "enabled": true,
    "deferred": false,
    "type": "stdio",
    "command": "/path/to/retailops/.venv/bin/python",
    "args": ["-m", "mcp_server.server"],
    "env_file": "/home/<agent-user>/.picoclaw/retailops.env"
  }
}
```

**On Windows:**

```bash
picoclaw mcp add retailops \
  --env-file "C:\Users\<user>\.picoclaw\retailops.env" \
  --no-deferred \
  -- "C:\path\to\retailops\.venv\Scripts\python.exe" -m mcp_server.server
```

```json
{
  "retailops": {
    "enabled": true,
    "deferred": false,
    "type": "stdio",
    "command": "C:\\path\\to\\retailops\\.venv\\Scripts\\python.exe",
    "args": ["-m", "mcp_server.server"],
    "env_file": "C:\\Users\\<user>\\.picoclaw\\retailops.env"
  }
}
```

**Why `PYTHONPATH` instead of `cwd`:** PicoClaw's MCP server schema has no
working-directory field — it supports `command`, `args`, `env`, `env_file`,
`url`, `headers`, `type`, and `deferred`. Since `python -m mcp_server.server`
resolves the package relative to the working directory, and PicoClaw inherits
its own instead, the repo root must be on `PYTHONPATH` for the import to
succeed. Putting it in the env file keeps it next to the other RetailOps
settings. Runtimes that *do* support `cwd`, or supervisors that set an
explicit working directory, are not automatically clear of every
working-directory dependency either — see "Linux-specific gotchas" (Gotcha 1)
below for a second, independent one inside the `mcp` library itself.

Other flags worth knowing: `--deferred` enables lazy tool discovery for this
server (§6), `-e KEY=value` sets individual variables inline (saved into
`config.json` in plain text — prefer `--env-file`), and `-t` selects the
transport for the remote case, paired with `-H "Authorization: Bearer <token>"`.

Related commands: `picoclaw mcp list`, `picoclaw mcp show retailops`,
`picoclaw mcp test retailops`, `picoclaw mcp remove retailops`.

> **Note:** `picoclaw mcp add` rewrites `config.json`, normalizing it and
> expanding every unset field to its default. Re-read the file afterwards if you
> hand-edited it. In the validated run this surfaced
> `tools.exec.allow_remote: true` — meaning chat messages can trigger shell
> commands — which had not been set explicitly.

#### Step 6: Verify

```bash
picoclaw mcp test retailops
```

Expected:

```
Connected to MCP server protocol=2025-11-25 server=retailops serverName=RetailOps serverVersion=1.28.1
Listed tools from MCP server server=retailops toolCount=59
✓ MCP server "retailops" reachable (59 tools).
```

(`serverVersion=1.30.0` in the Windows run — see "Validated configuration"
above; both satisfy `mcp>=1.27.0,<2`.)

This is rung 2 of §7 and passes **without the Django server running**. Continue
up the ladder from the chat channel:

```text
/list mcp
/show mcp retailops
```

Then, in conversation: "which products are low on stock?" (rungs 3–4), followed
by a write such as creating a sales order (rung 5).

This ladder has been walked to rung 6 end to end: a customer and a sales
order created through the Telegram channel (rung 5), and the `Staff` role's
boundary confirmed directly — attempting to confirm the order returned a real
`403` in the RetailOps server log for `POST .../confirm/`, not merely a
refusal from the model. That is the level of verification this section
assumes going forward.

#### Step 7: Run

Two long-running processes, in separate terminals:

```bash
cd /path/to/retailops && .venv/bin/python manage.py runserver
```

```bash
picoclaw gateway
```

**On Windows** — the same two processes, in separate terminals:

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py runserver
```

```bash
picoclaw gateway
```

The gateway starts the chat channels and the MCP connections. Without the Django
server, every RetailOps tool *call* fails with a connection error even though
registration succeeded.

**In production**, run PicoClaw under a supervisor instead of a bare
terminal — this is also what prevents Gotcha 1 below (the inherited-`cwd`
`.env` failure) from ever occurring, because `systemd` always sets an
explicit `WorkingDirectory` instead of passing through whatever `cwd` the
invoking process happened to have.

Create `/etc/systemd/system/picoclaw.service`:

```ini
[Unit]
Description=PicoClaw agent runtime (RetailOps integration)
After=network-online.target
Wants=network-online.target
# If RetailOps' own backend runs as a systemd unit too, add it here so
# PicoClaw does not start before the API is reachable, e.g.:
# After=network-online.target retailops.service

[Service]
Type=simple
User=<agent-user>
Group=<agent-user>
WorkingDirectory=/home/<agent-user>
ExecStart=/home/<agent-user>/picoclaw/bin/picoclaw gateway
Restart=on-failure
RestartSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/<agent-user>/.picoclaw
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
```

`ProtectHome=read-only` plus the `ReadWritePaths` exception is what lets a
locked-down unit still write to `~/.picoclaw/` without opening up the rest of
`/home`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now picoclaw.service
sudo systemctl status picoclaw.service
journalctl -u picoclaw.service -f
```

The RetailOps backend is still a separate long-running process, supervised
however you already run it in production too:

```bash
cd /path/to/retailops
.venv/bin/python manage.py runserver
```

#### Linux-specific gotchas

Two findings surfaced only when running PicoClaw as a dedicated system user
(`useradd --system --create-home --shell /usr/sbin/nologin`) with no group
overlap with the RetailOps application user. A single-account setup — the
development path above, and Windows — never exercises the multi-user
boundary that triggers either of them.

**Gotcha 1: an inherited `cwd` breaks the `mcp` library's own `.env` lookup,
not just the `mcp_server` import.**

Step 5 explains why PicoClaw needs `PYTHONPATH` in place of a `cwd` field: it
resolves `mcp_server.server` relative to the working directory it inherits.
That is not the only working-directory dependency in play. The `mcp` package
that `mcp_server/server.py` sits on top of builds its own `Settings` object
through `pydantic_settings.BaseSettings`, which independently searches for a
`.env` file **relative to the process's actual working directory** — a
mechanism entirely separate from `mcp_server/config.py`'s own `.env` handling,
which RetailOps resolves absolutely via `__file__` and is therefore unaffected
by `PYTHONPATH` either way.

If that inherited `cwd` is a directory the agent's system user cannot read —
typically because a command was run as `sudo -u <agent-user> <command>`
without `-i`, which keeps the *caller's* working directory instead of moving
to the target user's home — the failure is a permission error, not a missing
file:

```
File ".../pydantic_settings/sources/providers/dotenv.py", line 106, in _read_env_files
    if env_path.is_file() or env_path.is_fifo():
File ".../pathlib.py", line 894, in is_file
    return S_ISREG(self.stat().st_mode)
PermissionError: [Errno 13] Permission denied: '.env'
```

`picoclaw mcp test retailops` surfaces this as an opaque
`failed to connect: calling "initialize": EOF`, not the traceback above — the
traceback only appears in the MCP server subprocess's own stderr. If the
`.env` genuinely does not exist, `pydantic-settings` handles that quietly; a
`PermissionError` specifically means the process reached a directory it
cannot read, which is the signature to look for.

Fix: give the process a working directory it can actually read. Step 7's
systemd unit does this unconditionally via `WorkingDirectory=/home/<agent-user>`,
so the problem does not occur under systemd supervision. It surfaces only when
invoking PicoClaw manually as a different user — `cd` to that user's home
first (or use `sudo -iu <agent-user>`, which does the same) before running any
`picoclaw` command by hand.

**Gotcha 2: agent and backend on the same host, behind a reverse proxy —
`400` or `301` instead of a normal response.**

Applies when `RETAILOPS_BASE_URL` points directly at the RetailOps app server
on loopback (e.g. `http://127.0.0.1:8000/api/v1`) while real client traffic to
that same backend goes through a reverse proxy (Caddy, nginx) that terminates
TLS and sets `Host` and `X-Forwarded-Proto` — this is the proxy in front of
the RetailOps REST API itself, not the proxy `MCP_GUIDE.md` §16 describes for
a *remote MCP transport*; the two are unrelated. The development setup
above does not exhibit this — `manage.py runserver` on its own has no proxy
to bypass. Bypassing that proxy in production produces one of two symptoms,
both from the same cause:

- A generic HTML `400` page, no server-side traceback — Django's
  `DisallowedHost` check rejects the loopback request's `Host` header and logs
  nothing by design. An empty log plus a `Content-Length` around 143 bytes is
  the signature; a bad token instead produces a `401` with a JSON body from
  DRF, not HTML.
- A `301` redirect — `SECURE_SSL_REDIRECT` (on by default once `DEBUG=False`)
  redirects any request missing `X-Forwarded-Proto: https`, which the proxy
  normally adds and a direct loopback request never carries.

**Preferred fix:** route the agent's traffic through the same path a real
client uses, instead of loosening either Django setting for all traffic:

1. Add an entry to the agent host's own `/etc/hosts` resolving the backend's
   public domain to `127.0.0.1` — this only changes name resolution on the
   agent's machine, not public DNS or any other client.
2. Point `RETAILOPS_BASE_URL` at that domain over `https://`
   (`https://retailops.example.com/api/v1`) instead of the loopback address.

This puts the correct `Host` and `X-Forwarded-Proto` on every request and uses
the real TLS certificate (verified by name, so no certificate warning),
without narrowing `ALLOWED_HOSTS` or `SECURE_SSL_REDIRECT` for every other
client of the same backend. Adding `127.0.0.1` to `ALLOWED_HOSTS`, or
disabling `SECURE_SSL_REDIRECT`, resolves either symptom individually but
weakens a setting that protects all traffic, not just the agent's — treat
those as diagnostic confirmation, not as the fix to ship.

Both gotchas above are properties of the RetailOps backend under a
multi-user, proxied topology, not of PicoClaw specifically — §10 confirms
neither reproduces for OpenClaw, whose native `--cwd` MCP registration and
a from-the-start proxied backend URL remove each precondition
structurally.

#### PicoClaw troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `picoclaw status` shows `<provider> API: not set` although the key is in `.security.yml` | Secrets are keyed by `model_name`; the key does not match the name in `model_list` | Rename the `.security.yml` key to match `model_name` exactly |
| `config.json contains unknown field(s): build_info` | Config written by an older PicoClaw version cannot migrate to v3 | Back up and write a fresh v3 config; `workspace/` can be kept |
| `ModuleNotFoundError: No module named 'mcp_server'` | The stdio process cannot resolve the package; PicoClaw sets no `cwd` | Add `PYTHONPATH=<repo root>` to the env file (Step 4) |
| `failed to connect: calling "initialize": EOF` (only when invoking PicoClaw manually via `sudo -u <agent-user>`) | The `mcp` library's own `.env` lookup hits an unreadable inherited `cwd` (`PermissionError`, independent of `PYTHONPATH`) | Run under `systemd` (Step 7), or `sudo -iu <agent-user>` / `cd` to that user's home first — see "Linux-specific gotchas" (Gotcha 1) |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` mentioning that `FastMCP` was renamed to `MCPServer` | The venv has `mcp` 2.x installed; `mcp_server/` targets the v1 API | Reinstall from `requirements.txt`, which pins `mcp>=1.27.0,<2`. A venv built before that pin, or with a loosened constraint, can pick up 2.x |
| `mcp test` passes but every tool call fails with a connection error | Django dev server is not running | Start `manage.py runserver`; tool listing never touches the API |
| Tool calls return 401 / authentication errors | Token missing, wrong, or revoked | Check `RETAILOPS_API_TOKEN` in the env file; call `retailops_whoami` |
| Tool calls return 403 | The backing user's role does not permit that operation | Re-read §4 and pick the appropriate role, or use a different service user |
| Tool call to a same-host, proxied backend returns a raw HTML `400`, no traceback | `DisallowedHost` — the request bypassed the reverse proxy in front of the RetailOps API | Route through the proxy instead of loosening `ALLOWED_HOSTS` — see "Linux-specific gotchas" (Gotcha 2) |
| Tool call to a same-host, proxied backend returns `301` | `SECURE_SSL_REDIRECT` fires — missing `X-Forwarded-Proto` from bypassing the proxy | Same fix as the row above — see "Linux-specific gotchas" (Gotcha 2) |
| `The filename, directory name, or volume label syntax is incorrect` | The command was written for a POSIX shell but ran in cmd.exe — forward-slash `cd` paths and `;` chaining are both invalid there | Use backslash paths and `&&`, or run the command in PowerShell / Git Bash |
| Bot responds to strangers | `allow_from` is empty | Set `channel_list.<channel>.allow_from` to specific user IDs |
| Chat channel stops authenticating after running `picoclaw mcp add` | The CLI rewrote `.security.yml` and migrated the `channels:` block to `channel_list.<name>.settings`, discarding the token (v0.3.1) | Re-add the token; write it under both shapes (Step 3) and re-verify after every MCP CLI change |

---

### 10. Integration: OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) is a Node.js agent runtime
distributed via `npm`, with a single JSON5 configuration file and native MCP
client and server support. Three differences from §9 run through the rest of
this section: its MCP server registration accepts a working directory
natively, its default chat-channel authorization model is pairing rather
than a pre-configured allowlist, and it ships its own cross-platform service
installer instead of requiring a hand-written unit file.

**Validated configuration — Linux**

| Item | Value |
|---|---|
| OpenClaw version | 2026.9.4 |
| Platform | Linux x86_64 |
| Minimum hardware | 2 vCPU / 2GB RAM — the gateway process alone used ~370MB+ at idle; 1GB total was insufficient once combined with the RetailOps dev server, causing new SSH connections to the host to start failing under memory pressure. Under real Telegram traffic in a hardened production deployment the gateway settled around 470–520MB; production hardware itself wasn't separately stress-tested at the 2GB floor. |
| Deployment | `systemd` service, dedicated system user (production — see Step 7; the built-in service installer does not work for this kind of account, see "Linux-specific gotchas"); also validated running directly under the invoking user's own account, no service, no dedicated user (development) |
| Transport | stdio |
| Result | 59 tools against the RetailOps MCP server, Telegram pairing validated end to end, `Staff` role's `403` on order confirmation confirmed in the server log — validated in both a single-account development setup and a hardened, dedicated-account production deployment behind a reverse proxy; see "Linux-specific gotchas" for what did and didn't carry over from PicoClaw's own findings |
| Validated | 2026-09-11 |

Steps below default to the Linux layout you'd use for local development,
with production and Windows alternatives called out where they differ.

#### Step 1: Install OpenClaw

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

Installs under your own account, no `sudo` required. The installer
provisions Node.js 24 via NodeSource if it isn't already present, and — if
the global `npm` prefix isn't writable without elevated privileges — falls
back automatically to a user-local prefix (`~/.npm-global`), adding it to
`PATH` in `~/.bashrc`. Open a new shell, or `source ~/.bashrc; hash -r`,
then confirm:

```bash
openclaw --version
```

Expected: `OpenClaw 2026.9.4 (<build>)`.

In production, install under a dedicated account instead — same command,
same account-creation step as §9 Step 1; nothing about the OpenClaw
installer itself changes.

**On Windows:**

```powershell
iwr -useb https://openclaw.ai/install.ps1 | iex
openclaw --version
openclaw doctor
```

This installs the CLI directly; OpenClaw also publishes a GUI desktop
installer, not used here since it doesn't fit this document's command-line
flow. The Windows path above is documented by OpenClaw itself and was not
independently re-verified for this integration the way the Linux steps
were.

#### Step 2: Configure the runtime (onboarding)

OpenClaw keeps one configuration file, `~/.openclaw/openclaw.json` (JSON5),
instead of PicoClaw's split `config.json` / `.security.yml`. The
non-interactive onboarding flow writes it in one step:

```bash
openclaw onboard \
  --non-interactive \
  --accept-risk \
  --flow quickstart \
  --mode local \
  --auth-choice openrouter-api-key \
  --openrouter-api-key "<provider-api-key>" \
  --gateway-bind loopback \
  --skip-daemon \
  --skip-channels

openclaw models set openrouter/z-ai/glm-5.3-flash
```

Identical on Windows — no filesystem paths are involved.

| Flag | Why |
|---|---|
| `--mode local` | Runs the gateway on this machine rather than pointing at a remote one. |
| `--gateway-bind loopback` | Binds the gateway's WebSocket to `127.0.0.1` only — nothing on the network can reach it. |
| `--skip-daemon` | Don't install a service yet; Step 7 covers that, dev and production separately. |
| `--skip-channels` | Chat channels are configured in Step 6, after the MCP server exists. |

With `--skip-daemon`, onboarding ends with a benign warning —
`Gateway did not become reachable at ws://127.0.0.1:18789` — because no
gateway process is running yet to reach. The configuration is written
correctly regardless; there's nothing to fix here. The relevant parts of
the result:

```json
{
  "gateway": {
    "mode": "local",
    "port": 18789,
    "bind": "loopback",
    "auth": { "mode": "token", "token": "<generated>" }
  },
  "auth": {
    "profiles": {
      "openrouter:default": { "provider": "openrouter", "mode": "api_key" }
    }
  },
  "agents": { "defaults": { "model": { "primary": "openrouter/auto" } } }
}
```

(`models set`, run right after, updates `agents.defaults.model.primary` to
the model you chose.)

The `--flow quickstart` default selects a `tools.profile` of `"coding"`,
which includes `terminal` (opens and drives a shell) and `process`
(controls active exec sessions) — real command execution, reachable from
any chat channel once one is connected, on by default. OpenClaw's own
external documentation describes a tool literally named `exec` enabled
under a `mode=full` default; no tool by that name exists in the actual
catalog — `terminal`/`process` are what actually carry that risk. This is
OpenClaw's equivalent of PicoClaw's `tools.exec` finding in §9 Step 2's
table — review §8 before exposing the bot. Deny both explicitly:

```bash
echo '{"tools":{"deny":["exec","terminal","process"]}}' | openclaw config patch --stdin
```

`config patch` only accepts a patch via `--stdin` or `--file` — it rejects
a JSON object passed as a positional argument.

#### Step 3: Confirm secret storage

Unlike PicoClaw, there's no separate secrets file to write by hand — the
onboarding above already wrote the OpenRouter key and the gateway's auth
token into `openclaw.json` and a state database under `~/.openclaw/state/`,
both restricted to the owning account (`600`). Confirm:

```bash
openclaw secrets audit
```

Identical on Windows. This reports both values as `PLAINTEXT_FOUND` —
expected, and the same protection level `.security.yml` gets in §9 Step 3;
`openclaw doctor` flags the same `gateway.auth.token` finding in a
hardened production install too, with no change in severity. OpenClaw also
offers an optional, team-scoped secret store
(`openclaw secrets store set <name> --kind secret --value-file <path>`)
that keeps values referenced rather than inlined in the config file; it
wasn't used for this validated run — reconsidered specifically for
production, where a `secretRef` value takes the form
`{"source": "env"|"file"|"exec"|"store", "provider": ..., "id": ...}` (all
three fields required), and reaffirmed as not worth standing up a whole
secrets provider for one credential already sitting in a
`600`-permissioned file, the same reasoning that holds for a single-account
deployment.

#### Step 4: Provision a RetailOps token

Create a dedicated service account and mint its token in one step — same
principle as §4/§5, adapted to also create the account if it doesn't exist
yet:

```bash
cd /path/to/retailops
.venv/bin/python manage.py shell -c "from core.models import User, Role; from rest_framework.authtoken.models import Token; role=Role.objects.get(name=Role.STAFF); user,_=User.objects.get_or_create(email='<agent-service-account>', defaults={'first_name':'OpenClaw','last_name':'Dev','role':role,'is_active':True}); user.set_unusable_password(); user.save(update_fields=['password']); token,_=Token.objects.get_or_create(user=user); print('token prefix', token.key[:6])"
```

`set_unusable_password()` means this account authenticates with its API
token only — it can never log in through the web UI, matching a service
account that should have no interactive session at all.

**On Windows:**

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py shell -c "from core.models import User, Role; from rest_framework.authtoken.models import Token; role=Role.objects.get(name=Role.STAFF); user,_=User.objects.get_or_create(email='<agent-service-account>', defaults={'first_name':'OpenClaw','last_name':'Dev','role':role,'is_active':True}); user.set_unusable_password(); user.save(update_fields=['password']); token,_=Token.objects.get_or_create(user=user); print('token prefix', token.key[:6])"
```

#### Step 5: Register the RetailOps MCP server

```bash
openclaw mcp add retailops \
  --command /path/to/retailops/.venv/bin/python \
  --arg -m --arg mcp_server.server \
  --cwd /path/to/retailops \
  --env RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1 \
  --env RETAILOPS_API_TOKEN=<token-from-step-4> \
  --env RETAILOPS_TIMEOUT=30
```

**Why `--cwd` instead of `PYTHONPATH`:** OpenClaw's `mcp add` accepts a
working directory directly. `python -m mcp_server.server` resolves the
package relative to that directory rather than to whatever `cwd` the
gateway process happens to have, so the `PYTHONPATH` workaround §9 Step 5
documents for PicoClaw is unnecessary here.

**On Windows:**

```powershell
openclaw mcp add retailops `
  --command C:\path\to\retailops\.venv\Scripts\python.exe `
  --arg -m --arg mcp_server.server `
  --cwd C:\path\to\retailops `
  --env RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1 `
  --env RETAILOPS_API_TOKEN=<token-from-step-4> `
  --env RETAILOPS_TIMEOUT=30
```

`mcp add` probes the server before saving, so a successful run already
confirms connectivity — there's no separate "add, then test" split the way
PicoClaw has. To re-check later, or from a different terminal:

```bash
openclaw mcp probe retailops
```

Expected: `retailops: 59 tools, resources, prompts, Codex approval auto` —
the same underlying RetailOps MCP server PicoClaw validates, reached
through a different client. Unlike PicoClaw's `mcp test`, this output
doesn't include an MCP protocol version string, so this integration
documents the tool count only. There's also no flag equivalent to
PicoClaw's `--no-deferred`: OpenClaw has no deferred/lazy-loading mode for
MCP servers — a registered server is probed and connected eagerly, always.

`openclaw mcp doctor retailops --probe` additionally flags:

```
warning: env.RETAILOPS_API_TOKEN contains a literal sensitive value; prefer an environment-backed value outside committed config
```

Expected, given Step 3's default — the same class of finding `secrets
audit` already reported for the OpenRouter key.

#### Step 6: Connect a chat channel (Telegram)

```bash
openclaw channels add --channel telegram --token "<telegram-bot-token>"
openclaw channels list
```

Identical on Windows. Expected:
`Telegram default: installed, configured, enabled, token=***`.

That's the quick path for a literal token. In production, read it from a
restricted file instead:

```bash
openclaw channels add --channel telegram --token-file <path-to-token-file>
```

The flag is `--token-file` (kebab-case) — `--tokenFile`, the camelCase form
the config schema's `tokenFile` field name would suggest, is not a
recognized option and the CLI rejects it outright.

OpenClaw defaults to `dmPolicy: "pairing"` rather than PicoClaw's
pre-configured `allow_from` allowlist — instead of listing an approved
Telegram user ID up front, the bot pairs with whoever messages it first,
with an explicit approval step. This step only registers the account;
pairing itself happens in Step 8, once the gateway from Step 7 is actually
running and polling.

#### Step 7: Run

Two long-running processes, in separate terminals — the RetailOps backend
however you already run it (see `INSTALL.md`), and the gateway:

```bash
openclaw gateway run
```

For a background process that survives closing the terminal:

```bash
setsid nohup openclaw gateway --port 18790 > ~/openclaw-gateway.log 2>&1 < /dev/null &
disown
```

**On Windows**, run `openclaw gateway run` in its own terminal window —
there's no direct equivalent to `nohup`/`disown`; for something that
survives closing the window, use the production installer below instead
of trying to background it.

In production, try OpenClaw's own service installer first — it generates
and starts a `systemd` unit on Linux, a `launchd` agent on macOS, or a
Windows Scheduled Task, whichever applies to the machine it runs on:

```bash
openclaw gateway install
openclaw gateway status --json
```

Against a dedicated, non-interactive system account (the same kind of
account §9 Step 1 creates for PicoClaw), this fails outright:

```
SERVICE_DEFINITION_UNKNOWN: Service definition cannot be safely inspected.
```

— even after enabling `systemd` lingering for that account
(`loginctl enable-linger <agent-user>`) and exporting `XDG_RUNTIME_DIR` by
hand. It wants an active systemd **user** session, which a non-interactive
account can't structurally provide. See "Linux-specific gotchas" for the
full finding.

The validated production path is a hand-written `systemd` **system** unit
instead — the same hardening §9 Step 7 uses for PicoClaw, pointed at
OpenClaw:

```ini
[Unit]
Description=OpenClaw agent runtime (RetailOps integration)
After=network-online.target
Wants=network-online.target
# If RetailOps' own backend runs as a systemd unit too, add it here so
# OpenClaw does not start before the API is reachable, e.g.:
# After=network-online.target retailops.service

[Service]
Type=simple
User=<agent-user>
Group=<agent-user>
WorkingDirectory=/home/<agent-user>
ExecStart=/home/<agent-user>/.npm-global/bin/openclaw gateway run
Restart=on-failure
RestartSec=10

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/<agent-user>/.openclaw /home/<agent-user>/.cache
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictRealtime=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
```

Save as `/etc/systemd/system/openclaw.service`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw.service
sudo systemctl status openclaw.service
journalctl -u openclaw.service -f
```

`~/.cache` in `ReadWritePaths` is not optional — see "Linux-specific
gotchas" for what happens without it.

**On Windows**, `openclaw gateway install` wraps a generated `gateway.cmd`
script in a `gateway.vbs` launcher run from Task Scheduler, so the
background gateway doesn't pop a visible console window — this integration
didn't validate whether the same account-type restriction applies there.
Stop it with:

```powershell
schtasks /end /tn "OpenClaw Gateway"
```

or PowerShell's `Stop-ScheduledTask`.

#### Step 8: Verify

Unlike §9, where `mcp test` (Step 6) checks connectivity independently of
the chat channel, OpenClaw's Telegram channel can't respond — or issue a
pairing code — until the gateway from Step 7 is actually running. That's
why this integration's verification ladder comes last here instead of
before "Run."

Message the bot once. The first message from an unrecognized sender gets a
pairing code instead of a reply:

```text
OpenClaw: access not configured.
Your Telegram user id: <id>
Pairing code: <code>
Ask the bot owner to approve with:
openclaw pairing approve telegram <code>
```

Approve it:

```bash
openclaw pairing list --channel telegram --account default
openclaw pairing approve telegram <code>
```

Identical on Windows. The pending code exists only in the running gateway
process's memory, not on disk — if the gateway restarts before you approve
it, the code is gone and the sender has to message again for a new one.

This ladder (§7) has been walked to rung 6 end to end, the same as
PicoClaw's validated run: a real message returned the correct identity and
role (`Staff`); a test customer and a test sales order were created
through the Telegram channel; and attempting to confirm that order was
blocked, with the real `403` confirmed in the RetailOps server log, not
just the model's own account of what happened:

```
Forbidden: /api/v1/orders/2/confirm/
"POST /api/v1/orders/2/confirm/ HTTP/1.1" 403 89
```

When the confirmation failed, the agent twice suggested being given a
token with a higher role to complete it. Both suggestions were declined —
see §4 and §8. That refusal, not the suggestion, is the correct outcome:
the `Staff` role's boundary held exactly as configured.

#### Linux-specific gotchas

Both topologies in this section are now validated end to end: the
single-account development setup above, and a hardened, dedicated-system-
account production deployment behind a reverse proxy — the same kind of
multi-user topology that surfaced PicoClaw's own two gotchas in §9.

Retested directly against OpenClaw, neither of PicoClaw's gotchas
reproduces:

- The inherited-`cwd` `.env` lookup failure (§9 Gotcha 1) has no
  OpenClaw-side trigger — `mcp add`'s native `--cwd` (Step 5) sets the
  working directory explicitly at registration time, and the production
  unit below also sets `WorkingDirectory=` unconditionally, so there's no
  inherited, ambient `cwd` for anything to depend on in the first place.
- The `ALLOWED_HOSTS`/`SECURE_SSL_REDIRECT` reverse-proxy bypass (§9
  Gotcha 2) has no OpenClaw-side trigger either — `RETAILOPS_BASE_URL` was
  pointed at the backend's real public domain over `https://`, through the
  proxy, from the first production step, so the loopback-bypass condition
  that causes it never existed.

Neither absence means OpenClaw is immune to backend misconfiguration in
general — it means these two specific failure modes have no precondition
to trigger under OpenClaw's own architecture and the setup this
integration uses.

**Gotcha: the built-in service installer fails under a dedicated,
non-interactive account.**

```
SERVICE_DEFINITION_UNKNOWN: Service definition cannot be safely inspected.
```

`openclaw gateway install` (Step 7) produces this against a `nologin`
system account, even with `systemd` lingering enabled
(`loginctl enable-linger <agent-user>`) and `XDG_RUNTIME_DIR` exported by
hand. It expects an active systemd **user** session (`systemctl --user`),
which a non-interactive service account can't structurally provide
regardless of those workarounds. Fix: use the hand-written system unit in
Step 7 instead — the validated production path for this integration, not
a fallback.

**Gotcha: the gateway needs `~/.cache` writable, not just `~/.openclaw`.**

Under `ProtectSystem=strict` with only `~/.openclaw` in `ReadWritePaths`,
the unit crash-loops:

```
[openclaw] Reason: Unsafe fallback OpenClaw temp dir: /home/<agent-user>/.cache/openclaw-<uid>
```

OpenClaw writes its own temp files under the XDG cache directory,
independent of the config/workspace root. Fix: add
`/home/<agent-user>/.cache` to `ReadWritePaths` alongside
`/home/<agent-user>/.openclaw`, as shown in Step 7.

#### OpenClaw troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Onboarding ends with `Gateway did not become reachable` | Expected with `--skip-daemon` — no gateway is running yet to probe | Ignore it; confirm the config directly with `cat ~/.openclaw/openclaw.json` or `openclaw config get gateway` |
| `mcp doctor retailops --probe` warns about a literal value in `env.RETAILOPS_API_TOKEN` | Expected given Step 3's default plaintext storage | Ignore it for a single-account setup, or move the token into the team secret store (Step 3) |
| A Telegram sender's pairing code no longer works | The gateway restarted after the code was issued but before it was approved — codes live in memory only | Have the sender message the bot again for a fresh code |

---

### 11. Integration: Hermes Agent

**Status: not yet validated.**

[Hermes Agent](https://hermes-ai.net/) — same checklist as §10.

---

### 12. Cross-Runtime Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Connectivity probe succeeds, tool calls fail with connection errors | The API is not running, or `RETAILOPS_BASE_URL` is wrong | Start the Django server; confirm the base URL includes the `/api/v1` suffix and has no trailing slash |
| All tool calls return authentication errors | No token in the environment, or it was revoked | Call `retailops_whoami` to see the effective identity and token source; re-mint per §5 |
| Some tools work, others return 403 | Role-gated operations; the MCP layer has no per-tool ACL | Choose a role that covers the intended operations (§4) |
| Tool schemas missing or truncated on the model side | The runtime inlines all 59 schemas and the model's context is too small | Enable deferred tool loading, or move to a larger-context model (§6) |
| Sporadic 429 responses during autonomous loops | A named throttle scope was exceeded | Check the rate table in §8 and slow the loop; the limits are per-user |
| Remote MCP server refuses to start | Non-loopback bind without the required remote settings | Set `MCP_AUTH_MODE=retailops-token`, an `https://` `MCP_PUBLIC_BASE_URL`, and `MCP_ALLOWED_HOSTS` (`MCP_GUIDE.md` §16) |
| Agent works, then stops after a config edit by the runtime's own CLI | Some runtimes rewrite and normalize their config, expanding defaults | Re-read the config after any CLI-driven change; verify security-relevant fields |

For MCP-layer errors, error codes, and the response contract, see
`MCP_GUIDE.md` §17 and §18.
