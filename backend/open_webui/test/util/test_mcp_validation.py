import pytest
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_backend.management.mcp_manager import validate_command


def test_validate_command_valid():
    """Test that valid commands pass validation"""
    assert validate_command(["python", "script.py"]) is True
    assert validate_command(["npx", "-y", "something"]) is True


def test_validate_command_empty_list():
    """Test that empty command list raises ValueError"""
    with pytest.raises(ValueError, match="Command must include a valid executable"):
        validate_command([])


def test_validate_command_none():
    """Test that None raises ValueError"""
    with pytest.raises(ValueError, match="Command must include a valid executable"):
        validate_command(None)


def test_validate_command_invalid_type():
    """Test that command starting with non-string raises ValueError"""
    with pytest.raises(ValueError, match="Command must include a valid executable"):
        validate_command([123, "script.py"])


def test_validate_command_empty_string_executable():
    """Test that command starting with empty string raises ValueError"""
    with pytest.raises(ValueError, match="Command must include a valid executable"):
        validate_command(["", "script.py"])


def test_validate_command_whitespace_executable():
    """Test that command starting with whitespace string raises ValueError"""
    with pytest.raises(ValueError, match="Command must include a valid executable"):
        validate_command(["   ", "script.py"])


def test_validate_command_disallowed_executable():
    """Test that disallowed executable raises ValueError"""
    with pytest.raises(ValueError, match="Executable 'rm' is not allowed"):
        validate_command(["rm", "-rf", "/"])


def test_validate_command_python_c_flag():
    """Test that python -c is rejected"""
    with pytest.raises(
        ValueError, match=r"Interpreter flags \(like -c\) are not allowed"
    ):
        validate_command(["python", "-c", "import os; os.system('rm -rf /')"])


def test_validate_command_python_m_flag():
    """Test that python -m is rejected"""
    with pytest.raises(
        ValueError, match=r"Interpreter flags \(like -m\) are not allowed"
    ):
        validate_command(["python3", "-m", "http.server"])


def test_validate_command_node_eval():
    """Test that node -e is rejected"""
    with pytest.raises(
        ValueError, match=r"Interpreter flags \(like -e\) are not allowed"
    ):
        validate_command(["node", "-e", "console.log('bad')"])


def test_validate_command_npx_command_execution():
    """Test that npx -c is rejected"""
    with pytest.raises(ValueError, match=r"Execution flags .* are not allowed"):
        validate_command(["npx", "-c", "echo hacked"])


def test_validate_command_bypass_whitespace_flag():
    """Test if leading whitespace in flag bypasses the check"""
    with pytest.raises(ValueError, match=r"Interpreter flags .* are not allowed"):
        validate_command(["python", " -c"])


def test_validate_command_valid_script():
    """Test that python script.py with a path is accepted"""
    assert validate_command(["python", "/path/to/script.py"]) is True


def test_validate_command_interactive_mode_python():
    """Test that running python without arguments (interactive mode) is rejected"""
    with pytest.raises(ValueError, match="Command must include script/arguments"):
        validate_command(["python"])


def test_validate_command_interactive_mode_node():
    """Test that running node without arguments is rejected"""
    with pytest.raises(ValueError, match="Command must include script/arguments"):
        validate_command(["node"])


def test_validate_command_absolute_path_allowed():
    """Test that /usr/bin/python is allowed because basename is python"""
    assert validate_command(["/usr/bin/python", "script.py"]) is True


def test_validate_command_case_sensitivity():
    """Test 'Python' vs 'python'"""
    with pytest.raises(ValueError, match=r"Executable 'Python' is not allowed"):
        validate_command(["Python", "script.py"])
