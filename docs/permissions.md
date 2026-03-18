# Permissions & Identity System

## Overview

MCP Harbour uses a **default-deny, whitelist-based** permission model. Each identity has a **Policy** that declares exactly which tools on which servers it can access, and optionally restricts argument values.

No policy = no access. The gateway creates an empty `AgentPolicy(permissions={})` for identities without a policy file, which denies everything.

## Identity

Identities are created via `harbour identity create <name>`. Each identity gets:
- A name (stored in `config.json`)
- A key prefix for display (stored in `config.json`)
- A bcrypt-hashed API key (stored in system keyring, never in config files)

The agent authenticates by providing the API key via `harbour-bridge --token <key>`. The daemon derives the identity by checking the token against all stored hashes — the agent does not declare its identity name.

## Policy

Each identity can have a policy file at `~/.mcp-harbour/policies/<identity_name>.json`:

```json
{
  "identity_name": "coding-agent",
  "permissions": {
    "filesystem": [
      {
        "name": "read_text_file",
        "policies": [
          {
            "arg_name": "path",
            "match_type": "glob",
            "pattern": "/home/user/projects/**"
          }
        ]
      }
    ],
    "database": [
      {
        "name": "query",
        "policies": [
          {
            "arg_name": "sql",
            "match_type": "regex",
            "pattern": "^SELECT\\s.*"
          }
        ]
      }
    ]
  }
}
```

### ToolPermission

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Tool name or glob (e.g. `read_*`, `*`) |
| `policies` | `List[ArgumentPolicy]` | Argument-level restrictions |

### ArgumentPolicy

| Field | Type | Description |
|---|---|---|
| `arg_name` | `str` | Name of the argument to restrict |
| `match_type` | `str` | `glob` (default) or `regex` |
| `pattern` | `str` | Pattern to match against the argument value |

In the CLI, argument policies are positional. Glob is the default. Use the `re:` prefix for regex:

```bash
harbour permit allow agent filesystem --tool "read_text_file" "path=/home/user/**"
harbour permit allow agent db --tool "query" "sql=re:^SELECT\s.*" "db=production"
```

A glob pattern without wildcards is an exact match.

## Permission Engine

The `PermissionEngine` class evaluates access at runtime:

1. **Server check** — Is the server listed in the policy?
2. **Tool check** — Does any `ToolPermission.name` glob-match the requested tool?
3. **Argument check** — Do all `ArgumentPolicy`s pass for the given arguments?

If any check fails, the engine raises an `McpError` with GPARS error code `-31001` (`AUTHORIZATION_DENIED`).

## GPARS Error Codes

| Code | Constant | Meaning |
|------|----------|---------|
| `-31001` | `AUTHORIZATION_DENIED` | Operation violates the security policy |
| `-31002` | `SERVER_UNAVAILABLE` | Target MCP server is not reachable |

Error responses include a `gpars_code` field in the data payload:

```json
{
  "code": -31001,
  "message": "Tool 'write_file' on server 'filesystem' is not allowed.",
  "data": {"gpars_code": "AUTHORIZATION_DENIED"}
}
```

## How It Works in Practice

### Session Creation

When an agent connects, `HarbourGateway.create_session()`:
1. Loads the identity's policy (or creates an empty deny-all policy).
2. Creates a `PermissionEngine` from it.
3. Spawns per-client MCP server processes for servers listed in the policy.
4. Builds a tool→server cache for O(1) routing.
5. Creates an MCP Server instance with filtered `list_tools` and guarded `call_tool`.

### Tool Filtering

`list_tools` only returns tools that match at least one entry in the policy. Agents never see tools they aren't allowed to use.

### Call Authorization

`call_tool` checks the policy before forwarding to the MCP server. If denied, it returns `AUTHORIZATION_DENIED` without forwarding the request.
