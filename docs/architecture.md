# Architecture

MCP Harbour is the [GPARS](https://github.com/GPARS-org/GPARS) plane boundary enforcement point. It sits between AI agents (Cognitive Plane) and MCP servers (Action Plane), enforcing per-agent security policies defined by the user.

## High-Level Diagram

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│  VS Code    │    │   Claude    │    │   Cursor    │
│  (Agent)    │    │  (Agent)    │    │  (Agent)    │
└──────┬──────┘    └──────┬──────┘    └──────┬──────┘
       │                  │                  │
       │ harbour-bridge   │ harbour-bridge   │
       │ --token sk_A     │ --token sk_B     │
       │                  │                  │
       ▼                  ▼                  ▼
  ┌────────────────────────────────────────────────┐
  │              Harbour Daemon                     │
  │            (TCP 127.0.0.1:4767)                │
  │                                                 │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
  │  │Session A │  │Session B │  │Session C │     │
  │  │Policy:   │  │Policy:   │  │Policy:   │     │
  │  │read_file │  │*         │  │git only  │     │
  │  └──────────┘  └──────────┘  └──────────┘     │
  │                                                 │
  │      ┌─────────── Gateway ──────────────┐      │
  │      │ Aggregates tools from servers    │      │
  │      │ Filters by policy on list_tools  │      │
  │      │ Enforces policy on call_tool     │      │
  │      └──────────────────────────────────┘      │
  └──────────────┬──────────────┬──────────────────┘
                 │              │
        ┌────────▼──────┐ ┌────▼──────────┐
        │  filesystem   │ │     git       │
        │  MCP Server   │ │  MCP Server   │
        └───────────────┘ └───────────────┘
```

## Process Isolation

- **Stdio servers** — per-client instances. Each agent connection gets its own server process. Full isolation.
- **Streamable HTTP servers** — shared instances. One process handles all clients.

## Entry Points

| Entry point | Role | Plane |
|-------------|------|-------|
| `harbour` | Server admin CLI | Action Plane (user) |
| `harbour-bridge` | Agent stdio bridge | Cognitive Plane (agent) |

These are intentionally separate. The agent cannot access admin commands through `harbour-bridge`.

## Module Map

```
mcp_harbour/
├── main.py              # Admin CLI (harbour)
├── bridge.py            # Agent bridge (harbour-bridge) — stdlib only, no admin deps
├── models.py            # Pydantic data models
├── config.py            # ConfigManager, platform-aware paths
├── process_manager.py   # ServerProcess, HarbourDaemon
├── gateway.py           # HarbourGateway, session factory, MCP stream handling
├── permissions.py       # PermissionEngine, policy matching
└── errors.py            # GPARS error codes
```

## Data Flow

```
Agent ──harbour-bridge──▶ TCP:4767 ──▶ HarbourGateway ──▶ MCP Servers
```

### Connection Handshake

1. Bridge sends `{"auth": "harbour_sk_..."}` + newline
2. Daemon derives identity from token (iterates all identities, checks bcrypt hashes)
3. Daemon responds `{"status": "ok", "identity": "..."}` + newline
4. Standard MCP JSON-RPC traffic begins

The agent does not declare its identity — the daemon derives it from the token. The agent cannot influence how it is identified.

## GPARS Alignment

| GPARS Concept | MCP Harbour Implementation |
|---|---|
| Cognitive Plane | Agent + harbour-bridge |
| Action Plane | MCP servers + security policies |
| Plane Boundary | HarbourGateway (TCP listener + auth + policy enforcement) |
| Security Policy | Per-identity policy files in `~/.mcp-harbour/policies/` |
| AUTHORIZATION_DENIED | McpError with code -31001 |
| SERVER_UNAVAILABLE | McpError with code -31002 |
| Identity verification | Token → bcrypt hash lookup (agent cannot self-assert) |
| Default deny | No policy = empty AgentPolicy = all access denied |
