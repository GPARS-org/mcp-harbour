"""Tests for the Gateway session logic with mocked ship processes."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mcp.types import Tool, ListToolsResult, CallToolResult, TextContent
from mcp.shared.exceptions import McpError
from mcp_harbour.models import (
    Server,
    ServerType,
    Identity,
    AgentPolicy,
    ToolPermission,
    ArgumentPolicy,
)
from mcp_harbour.process_manager import ServerProcess, HarbourDaemon
from mcp_harbour.gateway import HarbourGateway
from mcp_harbour.errors import AUTHORIZATION_DENIED_CODE


# ─── Helpers ────────────────────────────────────────────────────────


def make_mock_process(server_name: str, tool_names: list[str]) -> ServerProcess:
    proc = MagicMock(spec=ServerProcess)
    proc.server_config = Server(name=server_name, command="echo")
    proc.session = MagicMock()

    mock_tools = []
    for name in tool_names:
        tool = Tool(
            name=name,
            description=f"Mock {name}",
            inputSchema={"type": "object", "properties": {}},
        )
        mock_tools.append(tool)

    proc.list_tools = AsyncMock(return_value=ListToolsResult(tools=mock_tools))

    call_result = CallToolResult(
        content=[TextContent(type="text", text=f"result from {server_name}")]
    )
    proc.call_tool = AsyncMock(return_value=call_result)
    proc.stop = AsyncMock()

    return proc


# ─── Session Creation Tests ─────────────────────────────────────────


def create_admin_policy(config_manager, servers=None):
    """Helper to create a wildcard policy for admin that allows all tools on given servers."""
    if servers is None:
        servers = ["filesystem"]
    perms = {s: [ToolPermission(name="*")] for s in servers}
    policy = AgentPolicy(identity_name="admin", permissions=perms)
    config_manager.save_policy(policy)


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_stdio_server_spawns_per_client(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file", "write_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("admin")

        gateway.daemon.spawn_stdio_instance.assert_called_once()
        assert len(owned) == 1
        assert owned[0] is mock_proc

    @pytest.mark.asyncio
    async def test_http_server_reuses_shared(self, config_manager, sample_http_server):
        config_manager.add_server(sample_http_server)
        policy = AgentPolicy(identity_name="admin", permissions={"web-search": [ToolPermission(name="*")]})
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        shared_proc = make_mock_process("web-search", ["search"])
        gateway.daemon.shared_processes["web-search"] = shared_proc

        session_server, owned = await gateway.create_session("admin")

        assert len(owned) == 0

    @pytest.mark.asyncio
    async def test_policy_filters_servers(self, config_manager, sample_server):
        config_manager.add_server(sample_server)

        policy = AgentPolicy(
            identity_name="git-only",
            permissions={"git": [ToolPermission(name="*")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()
        gateway.daemon.spawn_stdio_instance = AsyncMock()

        session_server, owned = await gateway.create_session("git-only")

        gateway.daemon.spawn_stdio_instance.assert_not_called()
        assert len(owned) == 0


# ─── Tool Listing Tests ─────────────────────────────────────────────


class TestListTools:
    @pytest.mark.asyncio
    async def test_admin_sees_all_tools(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process(
            "filesystem", ["read_file", "write_file", "delete_file"]
        )
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("admin")

        from mcp.types import ListToolsRequest

        result = await session_server.request_handlers[ListToolsRequest](MagicMock())
        assert len(result.root.tools) == 3

    @pytest.mark.asyncio
    async def test_restricted_identity_sees_filtered_tools(
        self, config_manager, sample_server
    ):
        config_manager.add_server(sample_server)

        policy = AgentPolicy(
            identity_name="reader",
            permissions={"filesystem": [ToolPermission(name="read_file")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process(
            "filesystem", ["read_file", "write_file", "delete_file"]
        )
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("reader")

        from mcp.types import ListToolsRequest

        result = await session_server.request_handlers[ListToolsRequest](MagicMock())
        tool_names = [t.name for t in result.root.tools]
        assert "read_file" in tool_names
        assert "write_file" not in tool_names
        assert "delete_file" not in tool_names

    @pytest.mark.asyncio
    async def test_glob_tool_filter(self, config_manager, sample_server):
        config_manager.add_server(sample_server)

        policy = AgentPolicy(
            identity_name="glob-reader",
            permissions={"filesystem": [ToolPermission(name="read_*")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process(
            "filesystem", ["read_file", "read_dir", "write_file"]
        )
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("glob-reader")

        from mcp.types import ListToolsRequest

        result = await session_server.request_handlers[ListToolsRequest](MagicMock())
        tool_names = [t.name for t in result.root.tools]
        assert set(tool_names) == {"read_file", "read_dir"}


# ─── Tool Call Tests ─────────────────────────────────────────────────


class TestCallTool:
    @pytest.mark.asyncio
    async def test_call_allowed_tool(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("admin")

        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = session_server.request_handlers[CallToolRequest]

        request = MagicMock()
        request.params = CallToolRequestParams(
            name="read_file", arguments={"path": "/tmp/test.txt"}
        )

        result = await handler(request)
        mock_proc.call_tool.assert_called_once_with(
            "read_file", {"path": "/tmp/test.txt"}
        )

    @pytest.mark.asyncio
    async def test_call_denied_tool_returns_gpars_error(self, config_manager, sample_server):
        """Calling a non-permitted tool should return a JSON-RPC error with AUTHORIZATION_DENIED.
        The MCP SDK catches McpError and converts it to a wire-format error response."""
        config_manager.add_server(sample_server)

        policy = AgentPolicy(
            identity_name="readonly",
            permissions={"filesystem": [ToolPermission(name="read_file")]},
        )
        config_manager.save_policy(policy)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file", "write_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        session_server, owned = await gateway.create_session("readonly")

        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = session_server.request_handlers[CallToolRequest]

        request = MagicMock()
        request.params = CallToolRequestParams(
            name="write_file", arguments={"path": "/etc/passwd", "content": "hacked"}
        )

        # The SDK catches McpError and wraps it into a CallToolResult with isError=True
        result = await handler(request)
        # Verify the tool was NOT actually called on the server
        mock_proc.call_tool.assert_not_called()
        # Verify the result is an error
        call_result = result.root
        assert call_result.isError is True
        assert len(call_result.content) == 1
        assert "not allowed" in call_result.content[0].text.lower()


# ─── Process Lifecycle Tests ─────────────────────────────────────────


class TestProcessLifecycle:
    @pytest.mark.asyncio
    async def test_owned_processes_can_be_stopped(self, config_manager, sample_server):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        mock_proc = make_mock_process("filesystem", ["read_file"])
        gateway.daemon.spawn_stdio_instance = AsyncMock(return_value=mock_proc)

        _, owned = await gateway.create_session("admin")

        for proc in owned:
            await proc.stop()

        mock_proc.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_sessions_get_separate_processes(
        self, config_manager, sample_server
    ):
        config_manager.add_server(sample_server)
        create_admin_policy(config_manager)

        gateway = HarbourGateway.__new__(HarbourGateway)
        gateway.config_manager = config_manager
        gateway.daemon = HarbourDaemon()

        proc_a = make_mock_process("filesystem", ["read_file"])
        proc_b = make_mock_process("filesystem", ["read_file"])

        gateway.daemon.spawn_stdio_instance = AsyncMock(side_effect=[proc_a, proc_b])

        _, owned_a = await gateway.create_session("admin")
        _, owned_b = await gateway.create_session("admin")

        assert len(owned_a) == 1
        assert len(owned_b) == 1
        assert owned_a[0] is not owned_b[0]
        assert gateway.daemon.spawn_stdio_instance.call_count == 2


# ─── HarbourDaemon Unit Tests ───────────────────────────────────────


class TestHarbourDaemon:
    def test_init_empty(self):
        daemon = HarbourDaemon()
        assert daemon.shared_processes == {}

    def test_get_shared_nonexistent(self):
        daemon = HarbourDaemon()
        assert daemon.get_shared_process("nope") is None

    @pytest.mark.asyncio
    async def test_stop_all_shared(self):
        daemon = HarbourDaemon()
        proc = make_mock_process("test", ["tool"])
        daemon.shared_processes["test"] = proc

        await daemon.stop_all_shared()
        proc.stop.assert_called_once()
        assert daemon.shared_processes == {}
