import os
import re
import subprocess
import sys
import types
import tempfile
import logging

from open_webui.env import SRC_LOG_LEVELS
from open_webui.models.functions import Functions
from open_webui.models.tools import Tools

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


def extract_frontmatter(content):
    """
    Extract frontmatter as a dictionary from the provided content string.
    """
    frontmatter = {}
    frontmatter_started = False
    frontmatter_ended = False
    frontmatter_pattern = re.compile(r"^\s*([a-z_]+):\s*(.*)\s*$", re.IGNORECASE)

    try:
        lines = content.splitlines()
        if len(lines) < 1 or lines[0].strip() != '"""':
            # The content doesn't start with triple quotes
            return {}

        frontmatter_started = True

        for line in lines[1:]:
            if '"""' in line:
                if frontmatter_started:
                    frontmatter_ended = True
                    break

            if frontmatter_started and not frontmatter_ended:
                match = frontmatter_pattern.match(line)
                if match:
                    key, value = match.groups()
                    frontmatter[key.strip()] = value.strip()

    except Exception as e:
        print(f"An error occurred: {e}")
        return {}

    return frontmatter


def replace_imports(content):
    """
    Replace the import paths in the content.
    """
    replacements = {
        "from utils": "from open_webui.utils",
        "from apps": "from open_webui.apps",
        "from main": "from open_webui.main",
        "from config": "from open_webui.config",
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    return content


def load_tools_module_by_id(toolkit_id, content=None):
    """
    Load a tool module by its ID from the database or from provided content.

    Security Note (CWE-94 - Code Injection):
        This function uses exec() to execute dynamic Python code. This is intentional
        for the plugin system. Access control is enforced at the API layer:

        - When content=None: Code is loaded from the Tools database
        - When content is provided: Caller provides new code to execute

        API Access Control (routers/tools.py):
        - POST /api/tools/create: Requires admin OR 'workspace.tools' permission
        - POST /api/tools/id/{id}/update: Requires admin OR owner/write access

        The 'workspace.tools' permission is:
        - Disabled by default (USER_PERMISSIONS_WORKSPACE_TOOLS_ACCESS=False)
        - Controlled by admins via Admin Panel -> Users & Access -> Groups -> Permissions -> "Tools Access"
        - Has UI warning: "Enabling this will allow users to upload arbitrary code"

        Applicable roles when permission enabled: admin, global_analyst, analyst, user
    """

    if content is None:
        tool = Tools.get_tool_by_id(toolkit_id)
        if not tool:
            raise Exception(f"Toolkit not found: {toolkit_id}")

        content = tool.content

        content = replace_imports(content)
        Tools.update_tool_by_id(toolkit_id, {"content": content})
    else:
        frontmatter = extract_frontmatter(content)
        # Install required packages found within the frontmatter
        install_frontmatter_requirements(frontmatter.get("requirements", ""))

    module_name = f"tool_{toolkit_id}"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module

    # Create a temporary file and use it to define `__file__` so
    # that it works as expected from the module's perspective.
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()
    try:
        with open(temp_file.name, "w", encoding="utf-8") as f:
            f.write(content)
        module.__dict__["__file__"] = temp_file.name

        # Executing the modified content in the created module's namespace
        exec(content, module.__dict__)
        frontmatter = extract_frontmatter(content)
        log.info(f"Loaded module: {module.__name__}")

        # Create and return the object if the class 'Tools' is found in the module
        if hasattr(module, "Tools"):
            return module.Tools(), frontmatter
        else:
            raise Exception("No Tools class found in the module")
    except Exception as e:
        log.error(f"Error loading module: {toolkit_id}: {e}")
        del sys.modules[module_name]  # Clean up
        raise e
    finally:
        os.unlink(temp_file.name)


def load_function_module_by_id(function_id, content=None):
    """
    Load a function module by its ID from the database or from provided content.

    Security Note (CWE-94 - Code Injection):
        This function uses exec() to execute dynamic Python code. This is intentional
        for the plugin system. Access control is enforced at the API layer:

        - When content=None: Code is loaded from the Functions database
        - When content is provided: Caller provides new code to execute

        API Access Control (routers/functions.py):
        - POST /api/functions/create: Requires admin only (get_admin_user)
        - POST /api/functions/id/{id}/update: Requires admin only (get_admin_user)
        - All other endpoints that call this function load from database only

        Only administrators can create or modify function code.
    """

    if content is None:
        function = Functions.get_function_by_id(function_id)
        if not function:
            raise Exception(f"Function not found: {function_id}")
        content = function.content

        content = replace_imports(content)
        Functions.update_function_by_id(function_id, {"content": content})
    else:
        frontmatter = extract_frontmatter(content)
        install_frontmatter_requirements(frontmatter.get("requirements", ""))

    module_name = f"function_{function_id}"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module

    # Create a temporary file and use it to define `__file__` so
    # that it works as expected from the module's perspective.
    temp_file = tempfile.NamedTemporaryFile(delete=False)
    temp_file.close()
    try:
        with open(temp_file.name, "w", encoding="utf-8") as f:
            f.write(content)
        module.__dict__["__file__"] = temp_file.name

        # Execute the modified content in the created module's namespace
        exec(content, module.__dict__)
        frontmatter = extract_frontmatter(content)
        log.info(f"Loaded module: {module.__name__}")

        # Create appropriate object based on available class type in the module
        if hasattr(module, "Pipe"):
            return module.Pipe(), "pipe", frontmatter
        elif hasattr(module, "Filter"):
            return module.Filter(), "filter", frontmatter
        elif hasattr(module, "Action"):
            return module.Action(), "action", frontmatter
        else:
            raise Exception("No Function class found in the module")
    except Exception as e:
        log.error(f"Error loading module: {function_id}: {e}")
        del sys.modules[module_name]  # Cleanup by removing the module in case of error

        Functions.update_function_by_id(function_id, {"is_active": False})
        raise e
    finally:
        os.unlink(temp_file.name)


def is_safe_package_name(package: str) -> bool:
    """
    Validate that a package name is safe for pip installation.

    Allows:
        - Package names: alphanumeric, underscores, hyphens, dots
        - Optional extras: package[extra1,extra2]
        - Optional version specifiers: ==, >=, <=, !=, ~=, <, >
        - Version numbers: digits, dots, letters (like 1.0.0a1)

    Rejects:
        - Shell metacharacters: ; & | ` $ ( ) { } < >
        - pip flags: anything starting with -
        - URLs: git+, http://, https://, ftp://
        - Local paths: /, ./, ../
        - Windows paths: backslashes

    Args:
        package: The package specification string (e.g., "requests>=2.28.0")

    Returns:
        True if the package name is safe, False otherwise
    """
    if not package or not package.strip():
        return False

    package = package.strip()

    # Reject dangerous patterns
    dangerous_patterns = [
        r"[;&|`$(){}]",
        r"^\s*-",
        r"(git\+|http://|https://|ftp://)",
        r"^\.\.?[/\\]",
        r"^[/\\]",
        r"\\",
        r"\s",
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, package):
            return False

    # Validate against safe pattern:
    # Examples: requests, requests[security], requests>=2.0, PyYAML>=5.0,<7.0
    safe_pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._\-\[\]=<>!~,.*+]*$")

    return bool(safe_pattern.match(package))


def install_frontmatter_requirements(requirements):
    """
    Install packages specified in frontmatter requirements.

    Security Note (CWE-78 - Command Injection):
        This function validates package names before passing them to pip to prevent
        command injection attacks. Only safe package name patterns are allowed.
        Dangerous patterns (URLs, paths, shell metacharacters) are rejected.

    Args:
        requirements: Comma-separated list of package specifications

    Raises:
        ValueError: If a package name contains unsafe characters or patterns
    """
    if requirements:
        req_list = [req.strip() for req in requirements.split(",")]
        for req in req_list:
            if not req:
                continue
            if not is_safe_package_name(req):
                log.warning(f"Rejected unsafe package requirement: {req}")
                raise ValueError(
                    f"Invalid package name format: '{req}'. "
                    "Package names must be alphanumeric with optional version specifiers. "
                    "URLs, paths, and shell metacharacters are not allowed."
                )
            log.info(f"Installing requirement: {req}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", req])
    else:
        log.info("No requirements found in frontmatter.")
