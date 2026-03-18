# CLI Reference

## `harbour` — Admin CLI

### Server Management

#### `harbour dock`

Register a new MCP server.

```bash
harbour dock \
  --name filesystem \
  --command "npx -y @modelcontextprotocol/server-filesystem" \
  --server-type stdio
```

| Option | Required | Default | Description |
|---|---|---|---|
| `--name` | Yes | — | Unique name for the server |
| `--command` | Yes | — | Command to launch the MCP server |
| `--args` | No | `[]` | Additional arguments |
| `--server-type` | No | `stdio` | Connection type (`stdio` or `http`) |

#### `harbour undock`

Remove a registered server.

```bash
harbour undock filesystem
```

#### `harbour list`

List all registered servers.

```bash
harbour list
```

#### `harbour inspect`

Show details of a specific server.

```bash
harbour inspect filesystem
```

---

### Daemon Management

#### `harbour start`

Start the daemon via the platform service manager.

```bash
harbour start
```

#### `harbour stop`

Stop the daemon via the platform service manager.

```bash
harbour stop
```

#### `harbour status`

Check if the daemon is running.

```bash
harbour status
```

#### `harbour serve`

Run the daemon in the foreground (for debugging).

```bash
harbour serve
harbour serve --host 0.0.0.0 --port 5000
```

| Option | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Host to bind |
| `--port` | `4767` | Port to bind |

---

### Identity Management

#### `harbour identity create`

Create a new identity and generate an API key.

```bash
harbour identity create my-agent
```

#### `harbour identity list`

List all identities with key prefixes.

```bash
harbour identity list
```

#### `harbour identity delete`

Delete an identity and its policy.

```bash
harbour identity delete my-agent
```

---

### Policy Management

#### `harbour permit allow`

Grant tool-level permissions to an identity for a server.

```bash
# Allow all tools on filesystem
harbour permit allow my-agent filesystem

# Allow only read_text_file with path restriction
harbour permit allow my-agent filesystem --tool "read_text_file" "path=/home/user/projects/**"

# Multiple argument policies
harbour permit allow my-agent db --tool "query" "sql=re:^SELECT\s.*" "db=production"
```

| Option | Default | Description |
|---|---|---|
| `--tool` | `*` | Tool name or glob pattern |

Argument policies are passed as positional arguments after identity and server.

Format: `arg=pattern` (glob, default) or `arg=re:pattern` (regex).

A glob pattern without wildcards is an exact match: `"mode=readonly"` matches only `"readonly"`.

#### `harbour permit show`

Display the policy for an identity.

```bash
harbour permit show my-agent
```

---

## `harbour-bridge` — Agent Bridge

Separate entry point with no admin capabilities. Used by MCP clients to connect to the daemon.

```bash
harbour-bridge --token harbour_sk_XXXX
harbour-bridge --token harbour_sk_XXXX --host 192.168.1.10 --port 5000
```

| Option | Required | Default | Description |
|---|---|---|---|
| `--token` | Yes | — | API key for authentication |
| `--host` | No | `127.0.0.1` | Daemon host |
| `--port` | No | `4767` | Daemon port |

MCP client configuration:

```json
{
  "mcpServers": {
    "harbour": {
      "command": "harbour-bridge",
      "args": ["--token", "harbour_sk_XXXX"]
    }
  }
}
```
