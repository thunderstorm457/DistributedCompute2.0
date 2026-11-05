import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


LABEL = "org.distributedcompute.node-agent"
INSTALL_DIR = Path.home() / "Library" / "Application Support" / "DistributedCompute" / "NodeAgent"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_DIR = Path.home() / "Library" / "Logs" / "DistributedCompute"


def build_launch_agent_plist(server_url, python_executable, install_dir, log_dir, interval):
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python_executable),
            "-m",
            "node_agent",
            "--server",
            server_url,
            "--interval",
            str(interval),
        ],
        "WorkingDirectory": str(install_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(Path(log_dir) / "node-agent.log"),
        "StandardErrorPath": str(Path(log_dir) / "node-agent-error.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }


def _ensure_macos():
    if sys.platform != "darwin":
        raise RuntimeError("LaunchAgent installation is available only on macOS")


def _launchctl(*arguments, allow_failure=False):
    result = subprocess.run(
        ["launchctl", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode and not allow_failure:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result


def install_service(server_url, interval=60):
    _ensure_macos()
    parsed_url = urlparse(server_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("The provisioning server URL must use HTTPS")
    if interval <= 0:
        raise ValueError("Heartbeat interval must be greater than zero")

    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    _launchctl("bootout", domain, str(PLIST_PATH), allow_failure=True)

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    package_dir = Path(__file__).resolve().parent
    for filename in ("__init__.py", "__main__.py", "client.py", "macos_service.py"):
        shutil.copy2(package_dir / filename, INSTALL_DIR / filename)
    shutil.copy2(package_dir / "requirements.txt", INSTALL_DIR / "requirements.txt")

    base_python = getattr(sys, "_base_executable", sys.executable)
    virtualenv_dir = INSTALL_DIR / "venv"
    subprocess.run([base_python, "-m", "venv", str(virtualenv_dir)], check=True)
    python_executable = virtualenv_dir / "bin" / "python"
    subprocess.run(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "-r",
            str(INSTALL_DIR / "requirements.txt"),
        ],
        check=True,
    )

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = build_launch_agent_plist(
        server_url, python_executable, INSTALL_DIR, LOG_DIR, interval
    )
    temporary_plist = PLIST_PATH.with_suffix(".plist.tmp")
    temporary_plist.write_bytes(plistlib.dumps(plist))
    temporary_plist.replace(PLIST_PATH)

    _launchctl("bootstrap", domain, str(PLIST_PATH))
    _launchctl("kickstart", "-k", target)


def uninstall_service():
    _ensure_macos()
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(PLIST_PATH), allow_failure=True)
    PLIST_PATH.unlink(missing_ok=True)
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)


def service_status():
    _ensure_macos()
    result = _launchctl(
        "print", f"gui/{os.getuid()}/{LABEL}", allow_failure=True
    )
    if result.returncode:
        return "Node Agent LaunchAgent is not loaded"
    return result.stdout.strip()