# RetailOps Agent Runtime Integration

## Table of Contents

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

## 1. What This Covers

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

## 2. Agent Runtime Requirements

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
| Working directory (`cwd`) per server | Preferred | `python -m mcp_server.server` resolves the package from the working directory. If the runtime cannot set `cwd`, set `PYTHONPATH` to the repo root through the environment instead — see §9 Step 5. |
| Lazy or deferred tool loading | Preferred | RetailOps exposes 59 tools. Runtimes that always inline every tool schema spend meaningful context and cost per turn. See §6. |
| Secret storage separate from main config | Preferred | Keeps the RetailOps token out of the file you would otherwise share or commit. |
| Per-user or per-channel allowlisting | Preferred | An agent reachable from a public chat channel is reachable by anyone who finds it, and it holds your RetailOps token. |

Record the result for each new runtime in its own section, so the next
integration starts from a known answer rather than a fresh investigation.

---

## 3. Choosing a Transport

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

## 4. Identity and Least Privilege

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

## 5. Provisioning a Token for an Agent

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

## 6. Tool Surface Sizing

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

## 7. Verification Ladder

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

## 8. Security Checklist Before Going Live

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

## 9. Integration: PicoClaw

[PicoClaw](https://github.com/sipeed/picoclaw) is an ultra-lightweight agent
runtime written in Go by Sipeed. It runs on very small hardware, supports many
chat channels (Telegram, Discord, Matrix, Slack, IRC, and others), has a cron
scheduler, and includes a native MCP client.

**Validated configuration**

| Item | Value |
|---|---|
| PicoClaw version | v0.3.1 |
| Platform | Windows x86_64 (binaries also published for Linux, macOS, FreeBSD, Android, and ARM/RISC-V/MIPS/LoongArch) |
| Transport | stdio |
| Result | 59 tools, MCP protocol `2025-11-25`, server `RetailOps 1.30.0` |

The reported server version tracks the installed `mcp` package, so it moves with
`requirements.txt`. RetailOps pins `mcp>=1.27.0,<2` because `mcp_server/` targets
the v1 API — see the troubleshooting table below.

Paths below use the Windows layout from the validated run. On Linux or macOS,
substitute `~/.picoclaw/` and the platform tarball; the configuration itself is
identical.

### Step 1: Install PicoClaw

Download the release asset for your platform along with the checksums file, and
verify before extracting.

```bash
gh release download v0.3.1 --repo sipeed/picoclaw \
  --pattern "picoclaw_Windows_x86_64.zip" \
  --pattern "picoclaw_0.3.1_checksums.txt"
sha256sum -c --ignore-missing picoclaw_0.3.1_checksums.txt
```

**Windows equivalent** for the verification step:

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

### Step 2: Configure the runtime

PicoClaw reads `~/.picoclaw/config.json` (override with `PICOCLAW_CONFIG`; move
the whole data root with `PICOCLAW_HOME`). Version 3 of the schema is what
v0.3.1 expects. A minimal configuration covering models, one chat channel, and
MCP:

```json
{
  "version": 3,
  "agents": {
    "defaults": {
      "workspace": "C:\\Users\\<user>\\.picoclaw\\workspace",
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

Points worth understanding rather than copying blindly:

| Field | Why it is set this way |
|---|---|
| `model_list[].provider` + `model` | Use the explicit two-field form. With `provider` omitted, PicoClaw treats the first `/` in `model` as the provider name — which mangles OpenRouter ids like `z-ai/glm-5.3-flash`, since those contain their own slash. |
| `agents.defaults.model_name` | Must match a `model_name` in `model_list`, and also keys the secret lookup in Step 3. |
| `channel_list.telegram.allow_from` | An empty array means **anyone** who finds the bot can drive it. Set your numeric Telegram user ID (obtainable from `@userinfobot`). |
| `channel_list.telegram.settings.token` | Omit it here; supply it via `.security.yml` (Step 3). |
| `heartbeat.enabled` | When true, the agent wakes on a timer and consumes model tokens while idle. Leave it off until you want that behavior. |
| `tools.exec` | PicoClaw's shell execution tool. Enabled by default and reachable from chat channels — review §8 before exposing the bot. |

### Step 3: Store secrets in `.security.yml`

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

```powershell
icacls "C:\Users\<user>\.picoclaw\.security.yml" /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
& C:\Users\<user>\picoclaw\bin\picoclaw.exe status
```

Expected: `Config: ... ✓`, `Workspace: ... ✓`, the model name you set, and a
`✓` next to your provider.

### Step 4: Provision the RetailOps token

Follow §5, writing the env file where PicoClaw will read it. This example uses
the demo `manager@retailops.local` account against a local database; for
anything beyond local testing, create a dedicated service user first (§4).

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py shell -c "from core.models import User; from rest_framework.authtoken.models import Token; t,_=Token.objects.get_or_create(user=User.objects.get(email='manager@retailops.local')); open(r'C:\Users\<user>\.picoclaw\retailops.env','w').write('RETAILOPS_BASE_URL=http://127.0.0.1:8000/api/v1\nRETAILOPS_API_TOKEN='+t.key+'\nRETAILOPS_TIMEOUT=30\nPYTHONPATH=C:\\path\\to\\retailops\n'); print('token prefix', t.key[:6])"
```

The `PYTHONPATH` line is not optional — see Step 5.

### Step 5: Register the RetailOps MCP server

```bash
picoclaw mcp add retailops \
  --env-file "C:\Users\<user>\.picoclaw\retailops.env" \
  --no-deferred \
  -- "C:\path\to\retailops\.venv\Scripts\python.exe" -m mcp_server.server
```

This writes the following into `config.json` under `tools.mcp.servers`:

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
settings.

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

### Step 6: Verify

```bash
picoclaw mcp test retailops
```

Expected:

```
Connected to MCP server protocol=2025-11-25 server=retailops serverName=RetailOps serverVersion=1.30.0
Listed tools from MCP server server=retailops toolCount=59
✓ MCP server "retailops" reachable (59 tools).
```

This is rung 2 of §7 and passes **without the Django server running**. Continue
up the ladder from the chat channel:

```text
/list mcp
/show mcp retailops
```

Then, in conversation: "which products are low on stock?" (rungs 3–4), followed
by a write such as creating a sales order (rung 5).

### Step 7: Run

Two long-running processes, in separate terminals:

```bash
cd /d "C:\path\to\retailops" && .venv\Scripts\python.exe manage.py runserver
```

```bash
picoclaw gateway
```

The gateway starts the chat channels and the MCP connections. Without the Django
server, every RetailOps tool *call* fails with a connection error even though
registration succeeded.

### PicoClaw troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `picoclaw status` shows `<provider> API: not set` although the key is in `.security.yml` | Secrets are keyed by `model_name`; the key does not match the name in `model_list` | Rename the `.security.yml` key to match `model_name` exactly |
| `config.json contains unknown field(s): build_info` | Config written by an older PicoClaw version cannot migrate to v3 | Back up and write a fresh v3 config; `workspace/` can be kept |
| `ModuleNotFoundError: No module named 'mcp_server'` | The stdio process cannot resolve the package; PicoClaw sets no `cwd` | Add `PYTHONPATH=<repo root>` to the env file (Step 4) |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` mentioning that `FastMCP` was renamed to `MCPServer` | The venv has `mcp` 2.x installed; `mcp_server/` targets the v1 API | Reinstall from `requirements.txt`, which pins `mcp>=1.27.0,<2`. A venv built before that pin, or with a loosened constraint, can pick up 2.x |
| `mcp test` passes but every tool call fails with a connection error | Django dev server is not running | Start `manage.py runserver`; tool listing never touches the API |
| Tool calls return 401 / authentication errors | Token missing, wrong, or revoked | Check `RETAILOPS_API_TOKEN` in the env file; call `retailops_whoami` |
| Tool calls return 403 | The backing user's role does not permit that operation | Re-read §4 and pick the appropriate role, or use a different service user |
| `El nombre de archivo, el nombre de directorio o la sintaxis de la etiqueta del volumen no son correctos` (or the English equivalent) | The command was written for a POSIX shell but ran in cmd.exe — forward-slash `cd` paths and `;` chaining are both invalid there | Use backslash paths and `&&`, or run the command in PowerShell / Git Bash |
| Bot responds to strangers | `allow_from` is empty | Set `channel_list.<channel>.allow_from` to specific user IDs |
| Chat channel stops authenticating after running `picoclaw mcp add` | The CLI rewrote `.security.yml` and migrated the `channels:` block to `channel_list.<name>.settings`, discarding the token (v0.3.1) | Re-add the token; write it under both shapes (Step 3) and re-verify after every MCP CLI change |

---

## 10. Integration: OpenClaw

**Status: not yet validated.**

To complete this section, work through §2 and record the answers, then follow
the same step structure used for PicoClaw:

- [ ] Transports supported (stdio, SSE, streamable-HTTP)
- [ ] Secret injection mechanism (`env` map, env file, external secret store)
- [ ] Whether a per-server working directory is configurable, or whether
      `PYTHONPATH` is needed as in §9 Step 5
- [ ] Deferred / lazy tool loading support, and the default (§6)
- [ ] Chat-channel allowlisting model
- [ ] Which RetailOps role the runtime should hold (§4)
- [ ] Verified tool count and MCP protocol version from the connectivity probe

---

## 11. Integration: Hermes Agent

**Status: not yet validated.**

[Hermes Agent](https://hermes-ai.net/) — same checklist as §10.

---

## 12. Cross-Runtime Troubleshooting

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
