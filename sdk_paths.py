"""Root resolution and the path sandbox for the MPC SDK MCP server.

Every path that comes from a tool call goes through this module. Nothing here runs a process.
Standard library only.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The exact set the MPC strips when it builds a folder name (same constant as tools/new_plugin.py).
ILLEGAL_FILENAME_CHARS = '"#@,;:<>*^|?\\/'

WRITABLE_EXTENSIONS = {".cpp", ".h", ".hpp", ".json", ".md"}
MAX_FILE_BYTES = 512 * 1024
MAX_NAME_LEN = 64
SKIP_DIRS = {"build", "dist", "__pycache__", ".git"}


class SandboxError(ValueError):
    """Raised when an input names something outside the allowed roots or is otherwise unsafe."""


def sdk_root() -> Path:
    env = os.environ.get("SDK_ROOT", "").strip()
    return Path(env).expanduser().resolve() if env else HERE.parent


def workspace() -> Path:
    env = os.environ.get("WORKSPACE", "").strip()
    return Path(env).expanduser().resolve() if env else sdk_root()


def plugins_dir() -> Path:
    return workspace() / "plugins"


def examples_dir() -> Path:
    return sdk_root() / "examples"


def state_dir() -> Path:
    """WORKSPACE/.mpc-mcp, created on demand with a .gitignore so its contents are never committed."""
    d = workspace() / ".mpc-mcp"
    gi = d / ".gitignore"
    if not gi.is_file():
        try:
            d.mkdir(parents=True, exist_ok=True)
            gi.write_text("*\n", encoding="utf-8")
        except OSError:
            pass
    return d


def python_exe() -> str:
    """Interpreter used to run the SDK scripts."""
    return os.environ.get("PYTHON", "").strip() or sys.executable


def mpc_host() -> str:
    return os.environ.get("MPC_HOST", "").strip()


def is_inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def check_plugin_name(name: str) -> str:
    """A plugin project name is a single folder name under plugins/ (spaces allowed, as in the SDK)."""
    if not isinstance(name, str):
        raise SandboxError("plugin name must be a string")
    n = name.strip()
    if not n:
        raise SandboxError("plugin name must not be empty")
    if len(n) > MAX_NAME_LEN:
        raise SandboxError("plugin name is longer than %d characters" % MAX_NAME_LEN)
    if "\x00" in n or any(ord(c) < 32 for c in n):
        raise SandboxError("plugin name contains control characters")
    bad = sorted(set(c for c in n if c in ILLEGAL_FILENAME_CHARS))
    if bad:
        raise SandboxError("plugin name contains characters the MPC does not allow: %s" % " ".join(bad))
    if n in (".", "..") or n.startswith("."):
        raise SandboxError("plugin name must not start with a dot")
    return n


def resolve_plugin(name: str, allow_examples: bool = True) -> Path:
    """Map a plugin name to its project folder: WORKSPACE/plugins/<name>, else SDK examples/<name>.

    Raises SandboxError if the name is unsafe, FileNotFoundError if no such project exists.
    """
    n = check_plugin_name(name)
    roots = [plugins_dir()]
    if allow_examples:
        roots.append(examples_dir())
    for root in roots:
        cand = root / n
        if cand.is_dir():
            real = cand.resolve()
            if not is_inside(real, root.resolve()):
                raise SandboxError("plugin folder %r is a link that leaves %s" % (n, root))
            return real
    raise FileNotFoundError("no plugin project named %r in %s%s" % (
        n, plugins_dir(), " or %s" % examples_dir() if allow_examples else ""))


def is_example(project: Path) -> bool:
    return is_inside(project, examples_dir().resolve())


def resolve_in_plugin(project: Path, rel: str) -> Path:
    """Resolve a relative path inside a plugin project, rejecting every way out."""
    if not isinstance(rel, str) or not rel.strip():
        raise SandboxError("path must be a non-empty relative path")
    if "\x00" in rel:
        raise SandboxError("path contains a null byte")
    if "\\" in rel:
        raise SandboxError("use forward slashes in paths")
    p = Path(rel)
    if p.is_absolute() or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise SandboxError("absolute paths are not allowed")
    if any(part == ".." for part in p.parts):
        raise SandboxError("'..' is not allowed in paths")
    root = project.resolve()
    target = (root / p).resolve()
    if not is_inside(target, root) or target == root:
        raise SandboxError("path %r leaves the plugin folder" % rel)
    return target


def check_writable(project: Path, target: Path, content: str) -> None:
    if is_example(project):
        raise SandboxError("examples are read-only; create your own plugin with new_plugin")
    rel_parts = target.relative_to(project.resolve()).parts
    if rel_parts and rel_parts[0] in SKIP_DIRS:
        raise SandboxError("%s/ is build output; write source files instead" % rel_parts[0])
    if target.suffix.lower() not in WRITABLE_EXTENSIONS:
        raise SandboxError("only %s files can be written" % " ".join(sorted(WRITABLE_EXTENSIONS)))
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise SandboxError("file is larger than %d KB" % (MAX_FILE_BYTES // 1024))
    if target.is_symlink():
        raise SandboxError("refusing to write through a symlink")


def source_files(project: Path):
    """Project files that affect a build (everything except build output), sorted."""
    root = project.resolve()
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        if rel_dir.parts and rel_dir.parts[0] in SKIP_DIRS:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not (not rel_dir.parts and d in SKIP_DIRS))
        for f in sorted(filenames):
            if f in (".DS_Store", "Thumbs.db"):
                continue
            out.append(Path(dirpath) / f)
    return out


def fingerprint(project: Path) -> str:
    """Hash of path, size and mtime of every source file. Changes whenever any source file changes."""
    h = hashlib.sha256()
    root = project.resolve()
    for f in source_files(root):
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(("%s\0%d\0%d\n" % (f.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()


def load_manifest(project: Path) -> dict:
    with open(project / "plugin.json", "r", encoding="utf-8") as fh:
        m = json.load(fh)
    if not isinstance(m, dict):
        raise ValueError("plugin.json must contain a JSON object")
    return m


def output_folder_name(manifest: dict) -> str:
    """Same rule as tools/build.py: '<manufacturer> - VST - <name>' minus illegal characters."""
    s = "%s - VST - %s" % (manifest["manufacturer"], manifest["name"])
    return "".join(c for c in s if c not in ILLEGAL_FILENAME_CHARS)


def rel_to_workspace(p: Path) -> str:
    p = Path(p).resolve()
    for root in (workspace(), sdk_root()):
        if is_inside(p, root):
            return p.relative_to(root).as_posix()
    return str(p)
