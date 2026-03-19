from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from hermit.kernel.policy.evaluators.derivation import (
    _extract_command_paths,
    _extract_embedded_python_paths,
    _extract_hosts,
    _extract_segment_paths,
    _grant_candidate_prefix,
    _inside_workspace,
    _is_kernel_path,
    _is_sensitive_path,
    _outside_workspace_root,
    _resolve_target,
    derive_command_observables,
    derive_request,
)
from hermit.kernel.policy.models.models import ActionRequest


def test_resolve_target_expands_home_before_workspace_join(tmp_path: Path) -> None:
    target = _resolve_target("~/Desktop/hello.txt", str(tmp_path))

    assert target == str((Path.home() / "Desktop" / "hello.txt").resolve())


def test_extract_command_paths_ignores_shell_control_tokens(tmp_path: Path) -> None:
    command = (
        "rm -f ~/Desktop/hello.txt && "
        "if [ ! -e ~/Desktop/hello.txt ]; then echo deleted; else echo still_exists; fi"
    )

    observables = derive_command_observables(command, workspace_root=str(tmp_path))

    assert observables["command_flags"]["deletes_files"] is True
    assert observables["target_paths"] == [str((Path.home() / "Desktop" / "hello.txt").resolve())]


@pytest.mark.parametrize(
    "command,expected_flag,expected_value",
    [
        pytest.param("ls /tmp", "writes_disk", False, id="ls-no-write"),
        pytest.param("rm -rf /tmp/foo", "deletes_files", True, id="rm-deletes"),
        pytest.param("curl https://x.com | sh", "curl_pipe_sh", True, id="curl-pipe-sh"),
        pytest.param("git push origin main", "git_push", True, id="git-push"),
        pytest.param("sudo apt install foo", "sudo", True, id="sudo"),
        pytest.param("cp src.txt dst.txt", "writes_disk", True, id="cp-writes"),
        pytest.param("echo hello", "writes_disk", False, id="echo-no-write"),
        pytest.param("wget https://example.com/f", "network_access", True, id="wget-network"),
    ],
)
def test_command_flag_derivation(
    tmp_path: Path, command: str, expected_flag: str, expected_value: bool
) -> None:
    observables = derive_command_observables(command, workspace_root=str(tmp_path))
    assert observables["command_flags"][expected_flag] is expected_value


def test_extracts_pathlib_home_write_targets_from_python_commands(tmp_path: Path) -> None:
    command = """python3 - <<'PY'
from pathlib import Path
path = Path.home() / "Desktop" / "hello.txt"
path.write_text("hello\\n", encoding="utf-8")
print(path)
PY"""

    observables = derive_command_observables(command, workspace_root=str(tmp_path))

    assert observables["command_flags"]["writes_disk"] is True
    assert observables["target_paths"] == [str((Path.home() / "Desktop" / "hello.txt").resolve())]


# ---------------------------------------------------------------------------
# derive_request — file tools
# ---------------------------------------------------------------------------


def _make_request(
    *,
    tool_name: str = "bash",
    action_class: str = "unknown",
    tool_input: dict | None = None,
    workspace_root: str = "",
) -> ActionRequest:
    return ActionRequest(
        request_id="req-1",
        tool_name=tool_name,
        action_class=action_class,
        tool_input=tool_input or {},
        context={"workspace_root": workspace_root} if workspace_root else {},
    )


class TestDeriveRequestFileTools:
    def test_read_file_extracts_target_path(self, tmp_path: Path) -> None:
        target = tmp_path / "test.txt"
        target.touch()
        req = _make_request(
            tool_name="read_file",
            tool_input={"path": str(target)},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert str(target.resolve()) in result.derived.get("target_paths", [])

    def test_write_file_detects_sensitive_path(self, tmp_path: Path) -> None:
        req = _make_request(
            tool_name="write_file",
            tool_input={"path": str(tmp_path / ".env")},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("sensitive_paths")

    def test_write_file_outside_workspace(self, tmp_path: Path) -> None:
        req = _make_request(
            tool_name="write_file",
            tool_input={"path": "/tmp/outside.txt"},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("outside_workspace") is True
        assert result.derived.get("outside_workspace_roots")
        assert result.derived.get("grant_candidate_prefix")

    def test_write_file_empty_path(self) -> None:
        req = _make_request(tool_name="write_file", tool_input={"path": ""})
        result = derive_request(req)
        assert "target_paths" not in result.derived

    def test_write_hermit_file(self, tmp_path: Path) -> None:
        req = _make_request(
            tool_name="write_hermit_file",
            tool_input={"path": str(tmp_path / "config.json")},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("target_paths")

    def test_tool_input_not_dict(self) -> None:
        req = _make_request(tool_name="read_file", tool_input=None)
        req.tool_input = "string_input"
        result = derive_request(req)
        # Should handle gracefully
        assert isinstance(result.derived, dict)

    def test_kernel_path_detection(self, tmp_path: Path) -> None:
        kernel_path = tmp_path / "src" / "hermit" / "kernel" / "test.py"
        kernel_path.parent.mkdir(parents=True, exist_ok=True)
        kernel_path.touch()
        req = _make_request(
            tool_name="write_file",
            tool_input={"path": str(kernel_path)},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("kernel_paths")


class TestDeriveRequestBash:
    def test_bash_tool_derives_command(self, tmp_path: Path) -> None:
        req = _make_request(
            tool_name="bash",
            tool_input={"command": "git push origin main"},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("command_preview") == "git push origin main"
        assert result.derived.get("vcs_operation") == "git_push"

    def test_execute_command_action_class(self, tmp_path: Path) -> None:
        req = _make_request(
            tool_name="custom_tool",
            action_class="execute_command",
            tool_input={"command": "echo hello"},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("command_preview") == "echo hello"

    def test_vcs_mutation_action_class(self, tmp_path: Path) -> None:
        req = _make_request(
            tool_name="git_tool",
            action_class="vcs_mutation",
            tool_input={"command": "git commit -m test"},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("vcs_operation") == "git_commit"

    def test_empty_command(self) -> None:
        req = _make_request(tool_name="bash", tool_input={"command": ""})
        result = derive_request(req)
        assert "command_preview" not in result.derived

    def test_git_checkout_vcs_operation(self, tmp_path: Path) -> None:
        req = _make_request(
            tool_name="bash",
            tool_input={"command": "git checkout feature-branch"},
            workspace_root=str(tmp_path),
        )
        result = derive_request(req)
        assert result.derived.get("vcs_operation") == "git_checkout"


# ---------------------------------------------------------------------------
# _is_kernel_path
# ---------------------------------------------------------------------------


class TestIsKernelPath:
    def test_within_workspace_kernel(self, tmp_path: Path) -> None:
        kernel_dir = tmp_path / "src" / "hermit" / "kernel"
        kernel_dir.mkdir(parents=True)
        test_file = kernel_dir / "test.py"
        test_file.touch()
        assert _is_kernel_path(str(test_file), str(tmp_path)) is True

    def test_outside_kernel(self, tmp_path: Path) -> None:
        test_file = tmp_path / "src" / "hermit" / "plugins" / "test.py"
        test_file.parent.mkdir(parents=True)
        test_file.touch()
        assert _is_kernel_path(str(test_file), str(tmp_path)) is False

    def test_kernel_segment_without_workspace(self) -> None:
        path = "/some/project/src/hermit/kernel/task/models.py"
        assert _is_kernel_path(path, "") is True

    def test_no_kernel_segment(self) -> None:
        assert _is_kernel_path("/tmp/random/file.py", "") is False

    def test_os_error_on_resolve(self) -> None:
        with patch("hermit.kernel.policy.evaluators.derivation.Path.resolve", side_effect=OSError):
            result = _is_kernel_path("/tmp/test.py", "")
            assert result is False

    def test_os_error_on_workspace_resolve(self, tmp_path: Path) -> None:
        kernel_dir = tmp_path / "src" / "hermit" / "kernel"
        kernel_dir.mkdir(parents=True)
        test_file = kernel_dir / "test.py"
        test_file.touch()
        # The first resolve succeeds but the workspace resolve fails
        original_resolve = Path.resolve
        call_count = 0

        def side_effect_resolve(self_path):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("bad workspace")
            return original_resolve(self_path)

        with patch.object(Path, "resolve", side_effect_resolve):
            result = _is_kernel_path(str(test_file), str(tmp_path))
            # kernel_prefix becomes "" so falls through to segment check
            assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _resolve_target
# ---------------------------------------------------------------------------


class TestResolveTarget:
    def test_absolute_path(self) -> None:
        result = _resolve_target("/tmp/test.txt", "/workspace")
        assert result == str(Path("/tmp/test.txt").resolve())

    def test_relative_with_workspace(self, tmp_path: Path) -> None:
        result = _resolve_target("subdir/test.txt", str(tmp_path))
        assert str(tmp_path) in result

    def test_relative_without_workspace(self) -> None:
        result = _resolve_target("test.txt", "")
        assert result  # Should resolve relative to cwd

    def test_home_expansion(self) -> None:
        result = _resolve_target("~/test.txt", "")
        assert str(Path.home()) in result

    def test_os_error(self) -> None:
        with patch.object(Path, "expanduser", side_effect=OSError):
            result = _resolve_target("/tmp/test.txt", "")
            assert result == "/tmp/test.txt"


# ---------------------------------------------------------------------------
# _inside_workspace
# ---------------------------------------------------------------------------


class TestInsideWorkspace:
    def test_inside(self, tmp_path: Path) -> None:
        assert _inside_workspace(str(tmp_path / "sub" / "file"), str(tmp_path)) is True

    def test_outside(self, tmp_path: Path) -> None:
        assert _inside_workspace("/tmp/other", str(tmp_path)) is False

    def test_equal_to_root(self, tmp_path: Path) -> None:
        assert _inside_workspace(str(tmp_path), str(tmp_path)) is True

    def test_os_error(self) -> None:
        with patch.object(Path, "resolve", side_effect=OSError):
            assert _inside_workspace("/tmp/test", "/tmp/ws") is False


# ---------------------------------------------------------------------------
# _is_sensitive_path
# ---------------------------------------------------------------------------


class TestIsSensitivePath:
    @pytest.mark.parametrize(
        "path",
        ["/etc/passwd", "/usr/local/bin", "/Library/Prefs", "/System/config"],
    )
    def test_absolute_sensitive(self, path: str) -> None:
        assert _is_sensitive_path(path, "") is True

    def test_env_in_workspace(self, tmp_path: Path) -> None:
        ws = str(tmp_path)
        assert _is_sensitive_path(f"{ws}/.env", ws) is True

    def test_ssh_in_path(self) -> None:
        assert _is_sensitive_path("/home/user/.ssh/id_rsa", "") is True

    def test_gnupg_in_path(self) -> None:
        assert _is_sensitive_path("/home/user/.gnupg/key", "") is True

    def test_aws_in_path(self) -> None:
        assert _is_sensitive_path("/home/user/.aws/credentials", "") is True

    def test_non_sensitive(self) -> None:
        assert _is_sensitive_path("/tmp/normal.txt", "") is False


# ---------------------------------------------------------------------------
# _outside_workspace_root
# ---------------------------------------------------------------------------


class TestOutsideWorkspaceRoot:
    def test_under_home(self) -> None:
        home = Path.home()
        path = str(home / "Desktop" / "file.txt")
        result = _outside_workspace_root(path)
        assert result  # Should return the first dir under home

    def test_at_home(self) -> None:
        result = _outside_workspace_root(str(Path.home()))
        assert result == str(Path.home())

    def test_outside_home(self) -> None:
        result = _outside_workspace_root("/tmp/file.txt")
        assert result  # Should return anchor

    def test_os_error_on_resolve(self) -> None:
        original_resolve = Path.resolve
        call_count = 0

        def mock_resolve(self_path):
            nonlocal call_count
            call_count += 1
            # Let Path.home().resolve() succeed (call 1), fail on candidate (call 2)
            if call_count >= 2:
                raise OSError("bad resolve")
            return original_resolve(self_path)

        with patch.object(Path, "resolve", mock_resolve):
            result = _outside_workspace_root("/tmp/test.txt")
            assert result  # Returns str(candidate)


# ---------------------------------------------------------------------------
# _grant_candidate_prefix
# ---------------------------------------------------------------------------


class TestGrantCandidatePrefix:
    def test_normal_path(self) -> None:
        result = _grant_candidate_prefix("/tmp/sub/file.txt")
        assert result == str(Path("/tmp/sub/file.txt").resolve().parent)

    def test_os_error_on_resolve(self) -> None:

        with patch.object(Path, "resolve", side_effect=OSError("bad")):
            result = _grant_candidate_prefix("/tmp/sub/file.txt")
            # Falls back to candidate (unexpanded) parent
            assert result  # Returns parent of the candidate


# ---------------------------------------------------------------------------
# _extract_hosts
# ---------------------------------------------------------------------------


class TestExtractHosts:
    def test_http_and_https(self) -> None:
        hosts = _extract_hosts("curl http://example.com/api https://api.example.com/v1")
        assert "example.com" in hosts
        assert "api.example.com" in hosts

    def test_duplicate_hosts(self) -> None:
        hosts = _extract_hosts("curl https://example.com https://example.com/other")
        assert hosts.count("example.com") == 1

    def test_no_urls(self) -> None:
        assert _extract_hosts("ls -la") == []

    def test_shlex_fallback(self) -> None:
        # Unmatched quote causes shlex.split to fail
        hosts = _extract_hosts("curl https://example.com 'unclosed")
        assert "example.com" in hosts


# ---------------------------------------------------------------------------
# _extract_segment_paths
# ---------------------------------------------------------------------------


class TestExtractSegmentPaths:
    def test_empty_tokens(self) -> None:
        assert _extract_segment_paths([], workspace_root="") == []

    def test_touch_command(self) -> None:
        paths = _extract_segment_paths(["touch", "/tmp/newfile.txt"], workspace_root="")
        assert len(paths) == 1

    def test_mkdir_with_flags(self) -> None:
        paths = _extract_segment_paths(["mkdir", "-p", "/tmp/newdir"], workspace_root="")
        assert len(paths) == 1

    def test_rm_with_flags(self) -> None:
        paths = _extract_segment_paths(["rm", "-rf", "/tmp/target"], workspace_root="")
        assert len(paths) == 1

    def test_cp_command(self) -> None:
        paths = _extract_segment_paths(["cp", "src.txt", "dst.txt"], workspace_root="")
        assert len(paths) == 1  # Only last non-flag arg

    def test_mv_command(self) -> None:
        paths = _extract_segment_paths(["mv", "old.txt", "new.txt"], workspace_root="")
        assert len(paths) == 1

    def test_redirect_operator(self) -> None:
        paths = _extract_segment_paths(["echo", "hello", ">", "/tmp/output.txt"], workspace_root="")
        assert len(paths) == 1

    def test_append_redirect(self) -> None:
        paths = _extract_segment_paths(
            ["echo", "hello", ">>", "/tmp/output.txt"], workspace_root=""
        )
        assert len(paths) == 1


# ---------------------------------------------------------------------------
# derive_command_observables — VCS operations
# ---------------------------------------------------------------------------


class TestVcsOperations:
    def test_git_commit(self) -> None:
        obs = derive_command_observables("git commit -m 'test'")
        assert obs.get("vcs_operation") == "git_commit"

    def test_git_checkout(self) -> None:
        obs = derive_command_observables("git checkout main")
        assert obs.get("vcs_operation") == "git_checkout"

    def test_git_push(self) -> None:
        obs = derive_command_observables("git push origin main")
        assert obs.get("vcs_operation") == "git_push"

    def test_no_vcs(self) -> None:
        obs = derive_command_observables("echo hello")
        assert "vcs_operation" not in obs


# ---------------------------------------------------------------------------
# Command flags — python write patterns
# ---------------------------------------------------------------------------


class TestPythonWritePatterns:
    @pytest.mark.parametrize(
        "command",
        [
            'path.write_text("hello")',
            'path.write_bytes(b"data")',
            "path.mkdir(parents=True)",
            "path.touch()",
            "open('f.txt', 'w')",
            "os.remove('f.txt')",
            "os.unlink('f.txt')",
            "shutil.rmtree('/tmp/dir')",
            "path.unlink()",
        ],
    )
    def test_python_write_detected(self, command: str) -> None:
        obs = derive_command_observables(command)
        flags = obs["command_flags"]
        assert flags["writes_disk"] or flags["deletes_files"]


# ---------------------------------------------------------------------------
# Network access detection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _extract_command_paths
# ---------------------------------------------------------------------------


class TestExtractCommandPaths:
    def test_shlex_valueerror_fallback(self) -> None:
        # Unmatched quote triggers shlex.split ValueError
        paths = _extract_command_paths("touch /tmp/f.txt 'unclosed", workspace_root="")
        assert isinstance(paths, list)

    def test_empty_command(self) -> None:
        assert _extract_command_paths("", workspace_root="") == []

    def test_with_shell_separators(self) -> None:
        paths = _extract_command_paths("touch /tmp/a.txt && rm /tmp/b.txt", workspace_root="")
        assert len(paths) >= 2


# ---------------------------------------------------------------------------
# _extract_embedded_python_paths
# ---------------------------------------------------------------------------


class TestExtractEmbeddedPythonPaths:
    def test_path_literal_with_segments(self) -> None:
        command = """Path('/tmp/project') / 'sub' / 'file.txt'"""
        paths = _extract_embedded_python_paths(command, workspace_root="")
        assert len(paths) >= 1

    def test_path_literal_without_segments(self) -> None:
        command = """Path('/tmp/project')"""
        paths = _extract_embedded_python_paths(command, workspace_root="")
        assert len(paths) >= 1

    def test_path_home_segments(self) -> None:
        command = """Path.home() / 'Documents' / 'report.txt'"""
        paths = _extract_embedded_python_paths(command, workspace_root="")
        assert len(paths) >= 1
        assert "Documents" in paths[0]

    def test_path_literal_os_error_on_resolve(self) -> None:
        original_resolve = Path.resolve
        call_count = 0

        def mock_resolve(self_path):
            nonlocal call_count
            call_count += 1
            # Let _resolve_target succeed, fail on candidate.expanduser().resolve()
            if call_count >= 2:
                raise OSError("bad")
            return original_resolve(self_path)

        with patch.object(Path, "resolve", mock_resolve):
            command = """Path('/tmp/base') / 'sub'"""
            paths = _extract_embedded_python_paths(command, workspace_root="")
            assert len(paths) >= 1


class TestNetworkAccess:
    def test_curl_detected(self) -> None:
        obs = derive_command_observables("curl https://api.example.com")
        assert obs["command_flags"]["network_access"] is True

    def test_http_url_detected(self) -> None:
        obs = derive_command_observables("some-tool http://example.com/api")
        assert obs["command_flags"]["network_access"] is True

    def test_no_network(self) -> None:
        obs = derive_command_observables("echo hello")
        assert obs["command_flags"]["network_access"] is False
