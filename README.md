# MCP Harbour

A security enforcement point for MCP servers. Sits between AI agents and MCP servers, enforcing per-agent security policies defined by the user.

Built as an implementation of the [GPARS](https://github.com/GPARS-org/GPARS) plane boundary — the user-controlled layer that verifies agent identity and governs what agents are permitted to do.

## Key Properties

- **Default deny** — no policy means no access.
- **Identity derived from token** — agents cannot self-assert their identity.
- **Per-agent policies** — each agent gets its own whitelist of servers, tools, and argument constraints.
- **GPARS error codes** — `AUTHORIZATION_DENIED` (-31001) and `SERVER_UNAVAILABLE` (-31002) as structured JSON-RPC errors.
- **Process isolation** — stdio servers are spawned per-client. No shared state between agent sessions.
- **Cross-platform** — works on Windows, macOS, and Linux.

## Installation

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/GPARS-org/mcp-harbour/main/scripts/install.sh | sh
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/GPARS-org/mcp-harbour/main/scripts/install.ps1 | iex
```

This installs the package (via `uv` or `pipx`), registers the daemon as a system service, and starts it automatically. The daemon starts on boot and restarts on crash.

Two commands are installed:
- `harbour` — server administration (manage servers, identities, policies, daemon)
- `harbour-bridge` — lightweight agent bridge (no admin access)

To uninstall, use `scripts/uninstall.sh` or `scripts/uninstall.ps1`.

## Quick Start

### 1. Dock MCP servers

```bash
harbour dock --name filesystem \
  --command "npx -y @modelcontextprotocol/server-filesystem /home/user/projects"

harbour dock --name bash --command "uvx mcp-server-bash"

harbour dock --name git --command "uvx mcp-server-git"
```

### 2. Create an identity

```bash
harbour identity create coding-agent
# Identity 'coding-agent' created successfully!
# API Key: harbour_sk_A7xK9m...
# Keep this key safe! It won't be shown again.
```

### 3. Define the security policy

```bash
# Allow all filesystem tools, restricted to a specific directory
harbour permit allow coding-agent filesystem \
  --tool "*" \
  --args "path=/home/user/projects/**"

# Allow bash execute
harbour permit allow coding-agent bash --tool "execute"

# Allow read-only git operations
harbour permit allow coding-agent git --tool "git_status"
harbour permit allow coding-agent git --tool "git_log"
harbour permit allow coding-agent git --tool "git_diff"
```

### 4. The daemon is already running

The install script registered and started the daemon automatically. Verify with:

```bash
harbour status
# Daemon is running.
```

### 5. Connect an agent

Configure your MCP client (Claude Desktop, VS Code, Cursor) with:

```json
{
  "mcpServers": {
    "harbour": {
      "command": "harbour-bridge",
      "args": ["--token", "harbour_sk_A7xK9m..."]
    }
  }
}
```

The agent sees a single MCP server exposing tools from all docked servers — filtered and enforced by its policy.

`harbour-bridge` is a separate entry point with no admin capabilities — the agent cannot access server management, identity, or policy commands.

## How It Works

```
Agent (MCP Client)
      │
      ▼
harbour-bridge --token sk_...      ← stdio bridge (no admin access)
      │
      ▼
127.0.0.1:4767                     ← TCP
      │
      ▼
HarbourGateway
├── Derive identity from token
├── Load per-agent security policy
├── Check every MCP request against policy
│   ├── Allowed → forward to MCP server
│   └── Denied → return AUTHORIZATION_DENIED
└── Server unreachable → return SERVER_UNAVAILABLE
      │
      ▼
MCP Servers (filesystem, bash, git, ...)
```

### What a denial looks like

If the agent tries to read `/etc/shadow` but the policy only allows `/home/user/projects/**`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -31001,
    "message": "Argument 'path' value '/etc/shadow' does not satisfy policy.",
    "data": {"gpars_code": "AUTHORIZATION_DENIED"}
  }
}
```

## CLI Reference

### Server Management

| Command | Description |
|---------|-------------|
| `harbour dock --name <name> --command <cmd>` | Register an MCP server |
| `harbour undock <name>` | Remove a server |
| `harbour list` | List all docked servers |
| `harbour inspect <name>` | Show server details |

### Identity Management

| Command | Description |
|---------|-------------|
| `harbour identity create <name>` | Create identity and generate API key |
| `harbour identity list` | List all identities |
| `harbour identity delete <name>` | Delete identity and its policy |

### Policy Management

| Command | Description |
|---------|-------------|
| `harbour permit allow <identity> <server>` | Grant permissions |
| `harbour permit show <identity>` | Show an identity's policy |

`permit allow` options:

| Option | Default | Description |
|--------|---------|-------------|
| `--tool` | `*` | Tool name or glob pattern |
| `--args` | — | Argument constraint: `arg_name=pattern` |

Argument patterns are auto-detected: `*` or `?` → glob, `^` or `$` → regex, otherwise exact match.

### Daemon

The daemon is managed by the platform service manager (systemd on Linux, launchd on macOS, Task Scheduler on Windows). It starts on boot and restarts on crash.

| Command | Description |
|---------|-------------|
| `harbour status` | Check if the daemon is running |
| `harbour start` | Start the daemon |
| `harbour stop` | Stop the daemon |
| `harbour serve` | Run in the foreground (for debugging) |

### Bridge (separate entry point)

| Command | Description |
|---------|-------------|
| `harbour-bridge --token <key>` | Connect an agent to the daemon |
| `harbour-bridge --token <key> --port 5000` | Connect on a custom port |

`harbour-bridge` is intentionally separate from `harbour`. It has no admin commands — agents cannot manage servers, identities, or policies through it.

## File Layout

Config directory: `~/.mcp-harbour` on Linux/macOS, `%APPDATA%\mcp-harbour` on Windows.

```
~/.mcp-harbour/
├── config.json              # Server registry + identity metadata
└── policies/
    ├── coding-agent.json    # Policy for "coding-agent"
    └── research-agent.json  # Policy for "research-agent"
```

API key hashes are stored in the system keyring, not in config files.

## Policy Format

Policies are per-identity JSON files mapping servers to allowed tools:

```json
{
  "identity_name": "coding-agent",
  "permissions": {
    "filesystem": [
      {
        "name": "read_file",
        "policies": [
          {
            "arg_name": "path",
            "match_type": "glob",
            "pattern": "/home/user/projects/**"
          }
        ]
      }
    ],
    "bash": [
      {
        "name": "execute",
        "policies": []
      }
    ]
  }
}
```

### Policy evaluation

1. **Server check** — is the server listed in the policy?
2. **Tool check** — does any tool permission glob-match the requested tool?
3. **Argument check** — do all argument constraints pass?

Any check fails → `AUTHORIZATION_DENIED`.

## Development

```bash
git clone https://github.com/GPARS-org/mcp-harbour.git
cd mcp-harbour
uv venv
source .venv/bin/activate    # or .venv\Scripts\activate on Windows
uv pip install -e ".[dev]"
```

This installs the package in editable mode — changes to the source take effect immediately. `harbour` and `harbour-bridge` commands point to your local code.

```bash
# Run tests
pytest

# Run the daemon locally
harbour serve

# Run the bridge locally
harbour-bridge --token harbour_sk_...
```

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md).

## Author

[Ismael Kaissy](https://github.com/GPARS-org)

## License

This project is licensed under the [MIT License](./LICENSE).
