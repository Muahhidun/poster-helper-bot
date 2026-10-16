"""MCP configuration policy with fake identities and temporary paths only."""

from dataclasses import replace
from pathlib import Path

import pytest

from furniture_mcp import config
from furniture_mcp.config import ConfigurationError, MCPSettings
from furniture_reader.config import ReaderSettings


ERROR = "Furniture MCP configuration is incomplete or invalid"
CHAT_A = "furniture-test-a@g.us"
CHAT_B = "furniture-test-b@g.us"
CHAT_C = "furniture-test-c@g.us"


@pytest.fixture
def valid_env(tmp_path):
    # macOS /var and /tmp can themselves be symlinks: use the canonical temp path.
    return {
        "FURNITURE_MCP_ENABLED": "true",
        "FURNITURE_MCP_AUTH0_ISSUER": "https://tenant.example.invalid/",
        "FURNITURE_MCP_RESOURCE_URL": "https://archive.example.invalid/mcp",
        "FURNITURE_MCP_OWNER_SUB": "auth0|owner",
        "FURNITURE_MCP_AUTH0_CLIENT_ID": "fakeclient",
        "FURNITURE_STORAGE_DIR": str(tmp_path.resolve() / "private-archive"),
        "FURNITURE_INSTANCE_ID": "fake-instance",
        "FURNITURE_CHAT_IDS": ",".join((CHAT_A, CHAT_B, CHAT_C)),
        "FURNITURE_READ_CHAT_IDS": ",".join((CHAT_A, CHAT_B)),
        "FURNITURE_MCP_CHAT_IDS": CHAT_A,
    }


def assert_invalid(env):
    with pytest.raises(ConfigurationError) as raised:
        MCPSettings.from_env(env)
    assert str(raised.value) == ERROR
    return raised.value


def test_empty_environment_is_disabled_without_reading_process_environment(monkeypatch):
    monkeypatch.setenv("FURNITURE_MCP_ENABLED", "true")
    assert MCPSettings.from_env({}) == MCPSettings()
    assert MCPSettings.from_env({}).chat_ids == frozenset()


@pytest.mark.parametrize("flag", ["", "false", "FALSE", "0", "1", "yes", "on", " true", "true "])
def test_disabled_configuration_does_not_validate_or_inspect_storage(valid_env, monkeypatch, flag):
    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled MCP must not validate paths")

    monkeypatch.setattr(ReaderSettings, "valid_storage", forbidden)
    valid_env.update(FURNITURE_MCP_ENABLED=flag, FURNITURE_MCP_MIN_ISSUED_AT="invalid",
                     FURNITURE_STORAGE_DIR="/not-a-real-storage-path")
    settings = MCPSettings.from_env(valid_env)
    assert settings == MCPSettings()
    settings.validate()


@pytest.mark.parametrize("flag", ["true", "TRUE", "True"])
def test_explicit_complete_configuration_and_nested_chat_scope(valid_env, flag):
    valid_env["FURNITURE_MCP_ENABLED"] = flag
    settings = MCPSettings.from_env(valid_env)
    assert settings.enabled
    assert settings.issuer == "https://tenant.example.invalid/"
    assert settings.resource == "https://archive.example.invalid/mcp"
    assert settings.owner_sub == "auth0|owner"
    assert settings.client_id == "fakeclient"
    assert settings.instance_id == "fake-instance"
    assert settings.allowed_chat_ids == frozenset({CHAT_A, CHAT_B, CHAT_C})
    assert settings.read_chat_ids == frozenset({CHAT_A, CHAT_B})
    assert settings.owner_chat_ids == settings.chat_ids == frozenset({CHAT_A})
    assert settings.min_issued_at == 0
    assert not Path(settings.storage_dir).exists()


@pytest.mark.parametrize("key", [
    "FURNITURE_MCP_AUTH0_ISSUER", "FURNITURE_MCP_RESOURCE_URL",
    "FURNITURE_MCP_OWNER_SUB", "FURNITURE_MCP_AUTH0_CLIENT_ID",
    "FURNITURE_STORAGE_DIR", "FURNITURE_INSTANCE_ID", "FURNITURE_CHAT_IDS",
    "FURNITURE_READ_CHAT_IDS", "FURNITURE_MCP_CHAT_IDS",
])
def test_enabled_configuration_requires_every_access_boundary(valid_env, key):
    del valid_env[key]
    assert_invalid(valid_env)


@pytest.mark.parametrize("key,valid_path", [
    ("FURNITURE_MCP_AUTH0_ISSUER", "/"),
    ("FURNITURE_MCP_RESOURCE_URL", "/mcp"),
])
@pytest.mark.parametrize("origin", [
    "http://tenant.example.invalid", "//tenant.example.invalid",
    "https://owner:fake-password@tenant.example.invalid",
    "https://owner@tenant.example.invalid", "https://tenant.example.invalid:443",
    "https://tenant.example.invalid:8443", "https://tenant.example.invalid:not-a-port",
    "https://TENANT.example.invalid", "https://tenant.example.invalid.",
    "https://tenant.example.invalid.evil@other.example.invalid",
    "https://localhost", "https://tenant.local", "https://[::1]",
    "https://[2001:db8::1]", "https://127.0.0.1", "https://169.254.169.254",
    "https://8.8.8.8", "https://tenant_example.invalid", "https://*.example.invalid",
    " https://tenant.example.invalid", "https://tenant.example.invalid\\other",
])
def test_identity_urls_reject_unsafe_or_noncanonical_origins(valid_env, key, valid_path, origin):
    valid_env[key] = origin + valid_path
    assert_invalid(valid_env)


@pytest.mark.parametrize("key,urls", [
    ("FURNITURE_MCP_AUTH0_ISSUER", (
        "https://tenant.example.invalid", "https://tenant.example.invalid/mcp",
        "https://tenant.example.invalid//", "https://tenant.example.invalid/?query=secret",
        "https://tenant.example.invalid/#fragment", "https://tenant.example.invalid/?",
        "https://tenant.example.invalid/#", "https://tenant.example.invalid/ ",
    )),
    ("FURNITURE_MCP_RESOURCE_URL", (
        "https://archive.example.invalid/", "https://archive.example.invalid/mcp/",
        "https://archive.example.invalid/other", "https://archive.example.invalid//mcp",
        "https://archive.example.invalid/%6dcp", "https://archive.example.invalid/mcp?query=secret",
        "https://archive.example.invalid/mcp#fragment", "https://archive.example.invalid/mcp?",
        "https://archive.example.invalid/mcp#", "https://archive.example.invalid/mcp ",
    )),
])
def test_identity_url_paths_queries_and_fragments_are_exact(valid_env, key, urls):
    for url in urls:
        assert_invalid({**valid_env, key: url})


@pytest.mark.parametrize("key", ["FURNITURE_MCP_OWNER_SUB", "FURNITURE_MCP_AUTH0_CLIENT_ID", "FURNITURE_INSTANCE_ID"])
@pytest.mark.parametrize("value", ["", "*", "with space", "tab\there", "line\nfeed", "nul\0value", "del\x7f", "не-ascii", "x" * 257])
def test_opaque_identity_values_are_explicit_bounded_and_not_wildcards(valid_env, key, value):
    valid_env[key] = value
    assert_invalid(valid_env)


@pytest.mark.parametrize("key", ["FURNITURE_MCP_OWNER_SUB", "FURNITURE_MCP_AUTH0_CLIENT_ID", "FURNITURE_INSTANCE_ID"])
def test_maximum_opaque_identity_length_is_valid(valid_env, key):
    valid_env[key] = "x" * 256
    assert MCPSettings.from_env(valid_env).enabled


@pytest.mark.parametrize("allowed,read,owner", [
    ("", CHAT_A, CHAT_A), (CHAT_A, "", CHAT_A), (CHAT_A, CHAT_A, ""),
    (CHAT_A, CHAT_B, CHAT_B),
    (f"{CHAT_A},{CHAT_B}", CHAT_A, CHAT_B),
    (f"{CHAT_A},{CHAT_B}", CHAT_A, f"{CHAT_A},{CHAT_B}"),
    (CHAT_A, f"{CHAT_A},{CHAT_B}", CHAT_A),
])
def test_empty_disjoint_and_widened_chat_scopes_fail_closed(valid_env, allowed, read, owner):
    valid_env.update(FURNITURE_CHAT_IDS=allowed, FURNITURE_READ_CHAT_IDS=read,
                     FURNITURE_MCP_CHAT_IDS=owner)
    assert_invalid(valid_env)


def test_chat_lists_trim_and_deduplicate_but_do_not_widen_owner_scope(valid_env):
    valid_env.update(FURNITURE_CHAT_IDS=f" {CHAT_A},,{CHAT_A}, {CHAT_B} , ",
                     FURNITURE_READ_CHAT_IDS=f"{CHAT_A}, {CHAT_A},",
                     FURNITURE_MCP_CHAT_IDS=f" {CHAT_A}, {CHAT_A} ")
    settings = MCPSettings.from_env(valid_env)
    assert settings.allowed_chat_ids == frozenset({CHAT_A, CHAT_B})
    assert settings.chat_ids == frozenset({CHAT_A})


@pytest.mark.parametrize("bad_chat", ["*", "with space", "tab\there", "line\nfeed", "nul\0value", "del\x7f", "не-ascii", "x" * 257])
def test_chat_values_are_validated_at_each_scope_layer(valid_env, bad_chat):
    for key in ("FURNITURE_CHAT_IDS", "FURNITURE_READ_CHAT_IDS", "FURNITURE_MCP_CHAT_IDS"):
        # Keep the nesting valid, so an invalid identity is the reason for rejection.
        environment = dict(valid_env)
        keys = ["FURNITURE_CHAT_IDS", "FURNITURE_READ_CHAT_IDS", "FURNITURE_MCP_CHAT_IDS"]
        for broader in keys[:keys.index(key) + 1]:
            environment[broader] += "," + bad_chat
        assert_invalid(environment)


@pytest.mark.parametrize("count,valid", [(1, True), (100, True), (101, False)])
def test_chat_scope_count_is_bounded(valid_env, count, valid):
    chats = ",".join(f"synthetic-{number}@g.us" for number in range(count))
    valid_env.update(FURNITURE_CHAT_IDS=chats, FURNITURE_READ_CHAT_IDS=chats,
                     FURNITURE_MCP_CHAT_IDS=chats)
    if valid:
        assert len(MCPSettings.from_env(valid_env).chat_ids) == count
    else:
        assert_invalid(valid_env)


@pytest.mark.parametrize("value", ["0", "1", "1700000000", "253402300799"])
def test_minimum_token_issue_time_accepts_inclusive_bounds(valid_env, value):
    valid_env["FURNITURE_MCP_MIN_ISSUED_AT"] = value
    assert MCPSettings.from_env(valid_env).min_issued_at == int(value)


@pytest.mark.parametrize("value", ["-1", "253402300800", "999999999999999999999999", "", "1.5", "nan", "true", "fake-secret"])
def test_minimum_token_issue_time_rejects_out_of_range_or_noninteger_values(valid_env, value):
    valid_env["FURNITURE_MCP_MIN_ISSUED_AT"] = value
    assert_invalid(valid_env)


@pytest.mark.parametrize("value", [True, False, 1.0, "1", None])
def test_direct_settings_validation_rejects_noninteger_minimum_issue_time(valid_env, value):
    settings = replace(MCPSettings.from_env(valid_env), min_issued_at=value)
    with pytest.raises(ConfigurationError, match=f"^{ERROR}$"):
        settings.validate()


@pytest.mark.parametrize("path", ["", ".", "relative/archive", "/"])
def test_storage_must_be_an_explicit_absolute_nonroot_path(valid_env, path):
    valid_env["FURNITURE_STORAGE_DIR"] = path
    assert_invalid(valid_env)


@pytest.mark.parametrize("relative", [".", "static", "templates", "static/nested", "furniture_mcp/archive"])
def test_storage_cannot_be_inside_application_or_public_tree(valid_env, relative):
    valid_env["FURNITURE_STORAGE_DIR"] = str(config.APP_ROOT / relative)
    assert_invalid(valid_env)


@pytest.mark.parametrize("nested", [False, True])
def test_storage_symlink_or_symlink_ancestor_is_rejected(valid_env, tmp_path, nested):
    root = tmp_path.resolve()
    target = root / "private-target"
    target.mkdir(mode=0o700)
    link = root / "linked-archive"
    link.symlink_to(target, target_is_directory=True)
    valid_env["FURNITURE_STORAGE_DIR"] = str(link / "child" if nested else link)
    assert_invalid(valid_env)
    assert list(target.iterdir()) == []


def test_existing_storage_validation_is_read_only(valid_env):
    root = Path(valid_env["FURNITURE_STORAGE_DIR"])
    root.mkdir(mode=0o700)
    sentinel = root / "sentinel.txt"
    sentinel.write_text("synthetic untouched archive marker")
    before = (root.stat().st_mode, sentinel.stat().st_mtime_ns, sentinel.read_bytes())
    assert MCPSettings.from_env(valid_env).storage_dir == str(root)
    assert (root.stat().st_mode, sentinel.stat().st_mtime_ns, sentinel.read_bytes()) == before
    assert list(root.iterdir()) == [sentinel]


@pytest.mark.parametrize("key,value", [
    ("FURNITURE_MCP_AUTH0_ISSUER", "https://owner:synthetic-secret-marker@tenant.example.invalid/"),
    ("FURNITURE_MCP_RESOURCE_URL", "https://archive.example.invalid/mcp?token=synthetic-secret-marker"),
    ("FURNITURE_MCP_OWNER_SUB", "synthetic-secret-marker with spaces"),
    ("FURNITURE_MCP_MIN_ISSUED_AT", "synthetic-secret-marker"),
])
def test_configuration_errors_never_include_supplied_sensitive_values(valid_env, key, value):
    valid_env[key] = value
    error = assert_invalid(valid_env)
    assert "synthetic-secret-marker" not in str(error)
    assert "synthetic-secret-marker" not in repr(error)
    assert error.__cause__ is None
    assert error.__suppress_context__
