import json
import logging
from contextlib import asynccontextmanager
from fnmatch import fnmatch
from typing import List, Optional, Dict, Tuple

import anyio
import keyring
import bcrypt
import mcp.types as types
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.shared.message import SessionMessage

from .config import ConfigManager
from .process_manager import HarbourDaemon, ServerProcess
from .permissions import PermissionEngine
from .errors import authorization_denied, server_unavailable
from .models import ServerType, AgentPolicy

logger = logging.getLogger("mcp_harbour")


class HarbourGateway:
    def __init__(self):
        self.config_manager = ConfigManager()
        self.daemon = HarbourDaemon()

    def _resolve_identity_from_token(self, token: str) -> Optional[str]:
        for name in self.config_manager.config.identities:
            try:
                hashed_key = keyring.get_password("mcp-harbour", name)
                if hashed_key and bcrypt.checkpw(token.encode(), hashed_key.encode()):
                    return name
            except Exception as e:
                logger.error(f"Keyring error checking identity '{name}': {e}")
        return None

    async def create_session(
        self, identity_name: str
    ) -> Tuple[Server, List[ServerProcess]]:
        policy = self.config_manager.load_policy(identity_name)
        if not policy:
            policy = AgentPolicy(identity_name=identity_name, permissions={})
        engine = PermissionEngine(policy)

        session_processes: Dict[str, ServerProcess] = {}
        owned_processes: List[ServerProcess] = []
        tool_server_map: Dict[str, str] = {}

        for server_config in self.config_manager.list_servers():
            server_name = server_config.name

            if server_name not in policy.permissions:
                continue

            if server_config.server_type == ServerType.stdio:
                try:
                    proc = await self.daemon.spawn_stdio_instance(server_config)
                    session_processes[server_name] = proc
                    owned_processes.append(proc)
                    logger.info(
                        f"Spawned stdio instance of '{server_name}' for identity '{identity_name}'"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to spawn '{server_name}' for '{identity_name}': {e}"
                    )
            else:
                shared = self.daemon.get_shared_process(server_name)
                if shared and shared.session:
                    session_processes[server_name] = shared

        for server_name, process in session_processes.items():
            if not process.session:
                continue
            try:
                ship_tools = await process.list_tools()
                for tool in ship_tools.tools:
                    tool_server_map[tool.name] = server_name
            except Exception as e:
                logger.error(f"Error listing tools from {server_name}: {e}")

        session_server = Server("mcp-harbour")

        @session_server.list_tools()
        async def list_tools() -> List[Tool]:
            all_tools = []

            for server_name, process in session_processes.items():
                if not process.session:
                    continue

                try:
                    ship_tools = await process.list_tools()

                    allowed_tools = []
                    for tool in ship_tools.tools:
                        for perm in policy.permissions.get(server_name, []):
                            if fnmatch(tool.name, perm.name):
                                allowed_tools.append(tool)
                                break
                    all_tools.extend(allowed_tools)

                except Exception as e:
                    logger.error(f"Error listing tools from {server_name}: {e}")

            return all_tools

        @session_server.call_tool()
        async def call_tool(name: str, arguments: dict) -> List[TextContent]:
            server_name = tool_server_map.get(name)

            if not server_name:
                raise authorization_denied(f"Tool '{name}' not found on any docked server.")

            process = session_processes.get(server_name)
            if not process or not process.session:
                raise server_unavailable(server_name)

            engine.check_permission(server_name, name, arguments)

            logger.info(f"Routing tool '{name}' to server '{server_name}'")
            try:
                result = await process.call_tool(name, arguments)
                return result.content
            except Exception as e:
                if hasattr(e, 'error'):
                    raise
                logger.error(f"Error calling tool '{name}' on '{server_name}': {e}")
                raise server_unavailable(server_name)

        return session_server, owned_processes

    async def start_shared_processes(self):
        for server in self.config_manager.list_servers():
            if server.server_type != ServerType.stdio:
                await self.daemon.start_shared_server(server)

    async def _handle_connection(self, stream):
        """Handle an authenticated agent connection."""
        owned_processes = []
        try:
            # 1. Handshake
            chunk = await stream.receive(4096)
            if b"\n" not in chunk:
                await stream.send(b'{"error": "Auth line too long"}\n')
                return

            line, remainder = chunk.split(b"\n", 1)

            try:
                auth_payload = json.loads(line.decode())
                token = auth_payload.get("auth")
            except Exception:
                await stream.send(b'{"error": "Invalid JSON"}\n')
                return

            if not token:
                await stream.send(b'{"error": "Missing auth token"}\n')
                return

            self.config_manager.reload()

            # 2. Authenticate
            identity_name = self._resolve_identity_from_token(token)
            if not identity_name:
                await stream.send(b'{"error": "Invalid token"}\n')
                return

            identity = self.config_manager.get_identity(identity_name)
            logger.info(f"Authenticated connection for {identity.name}")

            # 3. ACK
            await stream.send(
                b'{"status": "ok", "identity": "'
                + identity.name.encode()
                + b'"}\n'
            )

            # 4. Create Session
            session_server, owned_processes = await self.create_session(
                identity.name
            )

            # 5. Run Session
            async with _mcp_streams(stream) as (
                read_stream,
                write_stream,
            ):
                await session_server.run(
                    read_stream,
                    write_stream,
                    session_server.create_initialization_options(),
                )

        except Exception as e:
            logger.error(f"Handler error: {e}")
        finally:
            for proc in owned_processes:
                try:
                    await proc.stop()
                except Exception as e:
                    logger.error(f"Cleanup error: {e}")
            if owned_processes:
                logger.info(
                    f"Cleaned up {len(owned_processes)} per-client process(es)"
                )

    async def serve(self, host: str, port: int):
        """Run the gateway over TCP."""
        await self.start_shared_processes()

        try:
            listener = await anyio.create_tcp_listener(
                local_host=host, local_port=port
            )
        except OSError as e:
            if e.errno in (98, 48, 10048):  # EADDRINUSE: Linux=98, macOS=48, Windows=10048
                logger.error(f"Port {port} is already in use. Is another harbour instance running?")
                logger.error(f"Check with: harbour status")
                logger.error(f"Or use a different port: harbour serve --port <port>")
                raise SystemExit(1)
            raise

        logger.info(f"Listening on {host}:{port}")

        async def handler(stream):
            async with stream:
                await self._handle_connection(stream)

        async with listener:
            await listener.serve(handler)


@asynccontextmanager
async def _mcp_streams(stream):
    """Wraps a raw AnyIO ByteStream into MCP SessionMessage streams."""
    from anyio.streams.text import TextReceiveStream

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    text_stream = TextReceiveStream(stream)

    async def stream_reader():
        try:
            async with read_stream_writer:
                async for line in text_stream:
                    for part in line.splitlines():
                        if not part.strip():
                            continue
                        try:
                            message = types.JSONRPCMessage.model_validate_json(part)
                            await read_stream_writer.send(SessionMessage(message))
                        except Exception as exc:
                            await read_stream_writer.send(exc)
        except anyio.ClosedResourceError:
            pass
        except Exception as e:
            logger.error(f"Stream Reader Error: {e}")

    async def stream_writer():
        try:
            async with write_stream_reader:
                async for message in write_stream_reader:
                    if isinstance(message, Exception):
                        continue
                    try:
                        json_str = message.message.model_dump_json(
                            by_alias=True, exclude_none=True
                        )
                        await stream.send(json_str.encode() + b"\n")
                    except Exception as e:
                        logger.error(f"Serialization Error: {e}")
        except anyio.ClosedResourceError:
            pass
        except Exception as e:
            logger.error(f"Stream Writer Error: {e}")

    async with anyio.create_task_group() as tg:
        tg.start_soon(stream_reader)
        tg.start_soon(stream_writer)
        yield read_stream, write_stream
