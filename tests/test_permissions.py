"""Tests for the PermissionEngine."""

import pytest
from mcp.shared.exceptions import McpError
from mcp_harbour.models import AgentPolicy, ToolPermission, ArgumentPolicy
from mcp_harbour.permissions import PermissionEngine
from mcp_harbour.errors import AUTHORIZATION_DENIED_CODE


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def engine_read_only():
    policy = AgentPolicy(
        identity_name="readonly",
        permissions={
            "filesystem": [
                ToolPermission(
                    name="read_file",
                    policies=[
                        ArgumentPolicy(
                            arg_name="path",
                            match_type="glob",
                            pattern="/home/user/public/**",
                        )
                    ],
                )
            ]
        },
    )
    return PermissionEngine(policy)


@pytest.fixture
def engine_wildcard():
    policy = AgentPolicy(
        identity_name="admin",
        permissions={"filesystem": [ToolPermission(name="*", policies=[])]},
    )
    return PermissionEngine(policy)


@pytest.fixture
def engine_glob_tools():
    policy = AgentPolicy(
        identity_name="reader",
        permissions={"filesystem": [ToolPermission(name="read_*", policies=[])]},
    )
    return PermissionEngine(policy)


@pytest.fixture
def engine_multi_policy():
    policy = AgentPolicy(
        identity_name="strict",
        permissions={
            "database": [
                ToolPermission(
                    name="query",
                    policies=[
                        ArgumentPolicy(
                            arg_name="sql",
                            match_type="regex",
                            pattern=r"^SELECT\s.*",
                        ),
                        ArgumentPolicy(
                            arg_name="db",
                            match_type="exact",
                            pattern="readonly_db",
                        ),
                    ],
                )
            ]
        },
    )
    return PermissionEngine(policy)


def assert_authorization_denied(exc_info):
    """Helper to verify an McpError has the AUTHORIZATION_DENIED code."""
    assert exc_info.value.error.code == AUTHORIZATION_DENIED_CODE


# ─── Server-Level Permission Tests ──────────────────────────────────


class TestServerPermission:
    def test_allowed_server(self, engine_read_only):
        assert engine_read_only.check_permission(
            "filesystem", "read_file", {"path": "/home/user/public/file.txt"}
        )

    def test_denied_server(self, engine_read_only):
        with pytest.raises(McpError) as exc_info:
            engine_read_only.check_permission("git", "git_status")
        assert_authorization_denied(exc_info)

    def test_denied_unregistered_server(self, engine_wildcard):
        with pytest.raises(McpError) as exc_info:
            engine_wildcard.check_permission("database", "query")
        assert_authorization_denied(exc_info)


# ─── Tool-Level Permission Tests ────────────────────────────────────


class TestToolPermission:
    def test_exact_tool_allowed(self, engine_read_only):
        assert engine_read_only.check_permission(
            "filesystem", "read_file", {"path": "/home/user/public/x.txt"}
        )

    def test_exact_tool_denied(self, engine_read_only):
        with pytest.raises(McpError) as exc_info:
            engine_read_only.check_permission("filesystem", "write_file")
        assert_authorization_denied(exc_info)

    def test_wildcard_allows_all(self, engine_wildcard):
        assert engine_wildcard.check_permission("filesystem", "write_file")
        assert engine_wildcard.check_permission("filesystem", "delete_file")
        assert engine_wildcard.check_permission("filesystem", "read_file")

    def test_glob_match(self, engine_glob_tools):
        assert engine_glob_tools.check_permission("filesystem", "read_file")
        assert engine_glob_tools.check_permission("filesystem", "read_dir")

    def test_glob_no_match(self, engine_glob_tools):
        with pytest.raises(McpError) as exc_info:
            engine_glob_tools.check_permission("filesystem", "write_file")
        assert_authorization_denied(exc_info)

    def test_glob_no_match_partial(self, engine_glob_tools):
        with pytest.raises(McpError) as exc_info:
            engine_glob_tools.check_permission("filesystem", "delete_file")
        assert_authorization_denied(exc_info)


# ─── Argument Policy Tests ──────────────────────────────────────────


class TestArgumentPolicy:
    def test_glob_path_allowed(self, engine_read_only):
        assert engine_read_only.check_permission(
            "filesystem",
            "read_file",
            {"path": "/home/user/public/docs/readme.md"},
        )

    def test_glob_path_denied(self, engine_read_only):
        with pytest.raises(McpError) as exc_info:
            engine_read_only.check_permission(
                "filesystem",
                "read_file",
                {"path": "/etc/passwd"},
            )
        assert_authorization_denied(exc_info)

    def test_missing_required_arg(self, engine_read_only):
        with pytest.raises(McpError) as exc_info:
            engine_read_only.check_permission(
                "filesystem",
                "read_file",
                {"wrong_arg": "value"},
            )
        assert_authorization_denied(exc_info)

    def test_no_args_skips_policy(self, engine_read_only):
        assert engine_read_only.check_permission("filesystem", "read_file")

    def test_empty_args_skips_policy_like_none(self, engine_read_only):
        assert engine_read_only.check_permission("filesystem", "read_file", {})

    def test_regex_policy_allowed(self, engine_multi_policy):
        assert engine_multi_policy.check_permission(
            "database",
            "query",
            {"sql": "SELECT * FROM users", "db": "readonly_db"},
        )

    def test_regex_policy_denied(self, engine_multi_policy):
        with pytest.raises(McpError) as exc_info:
            engine_multi_policy.check_permission(
                "database",
                "query",
                {"sql": "DROP TABLE users", "db": "readonly_db"},
            )
        assert_authorization_denied(exc_info)

    def test_exact_policy_denied(self, engine_multi_policy):
        with pytest.raises(McpError) as exc_info:
            engine_multi_policy.check_permission(
                "database",
                "query",
                {"sql": "SELECT 1", "db": "production_db"},
            )
        assert_authorization_denied(exc_info)


# ─── Edge Cases ─────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_policy(self):
        engine = PermissionEngine(AgentPolicy(identity_name="empty", permissions={}))
        with pytest.raises(McpError) as exc_info:
            engine.check_permission("any", "any")
        assert_authorization_denied(exc_info)

    def test_policy_with_empty_tool_list(self):
        engine = PermissionEngine(
            AgentPolicy(identity_name="no_tools", permissions={"filesystem": []})
        )
        with pytest.raises(McpError) as exc_info:
            engine.check_permission("filesystem", "read_file")
        assert_authorization_denied(exc_info)


# ─── GPARS Error Code Tests ─────────────────────────────────────────


class TestGPARSErrorCodes:
    def test_denied_error_has_correct_code(self, engine_read_only):
        with pytest.raises(McpError) as exc_info:
            engine_read_only.check_permission("git", "git_status")
        assert exc_info.value.error.code == AUTHORIZATION_DENIED_CODE

    def test_denied_error_has_gpars_data(self, engine_read_only):
        with pytest.raises(McpError) as exc_info:
            engine_read_only.check_permission("git", "git_status")
        assert exc_info.value.error.data == {"gpars_code": "AUTHORIZATION_DENIED"}

    def test_denied_error_has_message(self, engine_read_only):
        with pytest.raises(McpError) as exc_info:
            engine_read_only.check_permission("git", "git_status")
        assert "denied" in exc_info.value.error.message.lower()
