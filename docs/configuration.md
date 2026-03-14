# Configuration

## Directory Layout

Config directory: `~/.mcp-harbour` on Linux/macOS, `%APPDATA%\mcp-harbour` on Windows.

```
~/.mcp-harbour/
├── config.json              # Server registry + identity metadata
└── policies/
    ├── coding-agent.json    # Policy for "coding-agent"
    └── research-agent.json  # Policy for "research-agent"
```

API key hashes are stored in the system keyring (via the `keyring` library), not in config files.

## config.json

```json
{
  "servers": {
    "filesystem": {
      "name": "filesystem",
      "command": "npx -y @modelcontextprotocol/server-filesystem",
      "args": [],
      "env": {},
      "server_type": "stdio",
    }
  },
  "identities": {
    "coding-agent": {
      "name": "coding-agent",
      "key_prefix": "harbour_sk_A7x..."
    }
  }
}
```

### Server Fields

| Field | Type | Description |
|---|---|---|
| `name` | `string` | Unique identifier |
| `command` | `string` | Launch command (supports multi-word, e.g. `npx -y ...`) |
| `args` | `string[]` | Additional CLI arguments |
| `env` | `object` | Extra environment variables |
| `server_type` | `"stdio" \| "http"` | Transport type |

### Identity Fields

| Field | Type | Description |
|---|---|---|
| `name` | `string` | Unique identifier |
| `key_prefix` | `string` | First 15 chars of API key (for display only) |

## Policy Files

Each identity can have a policy file at `policies/<name>.json`. See [Permissions](permissions.md) for the full schema.

## Python Constants

Defined in `mcp_harbour/config.py`:

| Constant | Value |
|---|---|
| `CONFIG_DIR` | `~/.mcp-harbour` (Linux/macOS) or `%APPDATA%\mcp-harbour` (Windows) |
| `CONFIG_FILE` | `<CONFIG_DIR>/config.json` |
| `POLICIES_DIR` | `<CONFIG_DIR>/policies` |
| `DEFAULT_HOST` | `127.0.0.1` |
| `DEFAULT_PORT` | `4767` |
