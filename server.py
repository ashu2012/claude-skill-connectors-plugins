"""
Local Filesystem MCP Server
============================
Exposes a configured local folder to Claude via the Model Context Protocol (MCP).
Provides tools to list, read, write, move, and delete files/folders,
all sandboxed inside ROOT_DIR so Claude cannot escape that directory.

Run:
    python server.py /path/to/your/folder

Then point Claude Desktop (or any MCP client) at this script over stdio.
See README.md for the exact config snippet.
"""

import sys
import os
import shutil
import fnmatch
from pathlib import Path
from datetime import datetime

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

if len(sys.argv) < 2:
    print("Usage: python server.py <root_directory>", file=sys.stderr)
    sys.exit(1)

ROOT_DIR = Path(sys.argv[1]).expanduser().resolve()

if not ROOT_DIR.exists() or not ROOT_DIR.is_dir():
    print(f"Error: '{ROOT_DIR}' is not a valid directory.", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("local-filesystem")


# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------

class PathEscapeError(Exception):
    """Raised when a requested path would resolve outside ROOT_DIR."""


def safe_resolve(relative_path: str) -> Path:
    """
    Resolve a user-supplied relative path against ROOT_DIR and guarantee
    the result stays inside ROOT_DIR (blocks '..' traversal, symlink escapes, etc).
    """
    candidate = (ROOT_DIR / relative_path).resolve()
    if ROOT_DIR != candidate and ROOT_DIR not in candidate.parents:
        raise PathEscapeError(
            f"Path '{relative_path}' resolves outside the allowed root directory."
        )
    return candidate


def rel(path: Path) -> str:
    """Return a path as a string relative to ROOT_DIR, for display purposes."""
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_directory(path: str = ".") -> str:
    """
    List files and subdirectories inside the given path (relative to the root folder).

    Args:
        path: Relative path within the root directory. Defaults to the root itself.
    """
    target = safe_resolve(path)
    if not target.exists():
        return f"Error: '{path}' does not exist."
    if not target.is_dir():
        return f"Error: '{path}' is not a directory."

    entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    if not entries:
        return f"'{path}' is empty."

    lines = []
    for entry in entries:
        kind = "DIR " if entry.is_dir() else "FILE"
        size = "" if entry.is_dir() else f" ({entry.stat().st_size} bytes)"
        lines.append(f"[{kind}] {entry.name}{size}")
    return "\n".join(lines)


@mcp.tool()
def read_file(path: str) -> str:
    """
    Read and return the full text content of a file.

    Args:
        path: Relative path to the file within the root directory.
    """
    target = safe_resolve(path)
    if not target.exists():
        return f"Error: '{path}' does not exist."
    if not target.is_file():
        return f"Error: '{path}' is not a file."

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: '{path}' does not appear to be a text file (binary content)."


@mcp.tool()
def write_file(path: str, content: str, mode: str = "overwrite") -> str:
    """
    Write text content to a file. Creates the file (and parent folders) if needed.

    Args:
        path: Relative path to the file within the root directory.
        content: Text content to write.
        mode: 'overwrite' (default, replaces file contents) or 'append'.
    """
    if mode not in ("overwrite", "append"):
        return "Error: mode must be 'overwrite' or 'append'."

    target = safe_resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    file_mode = "a" if mode == "append" else "w"
    with open(target, file_mode, encoding="utf-8") as f:
        f.write(content)

    action = "Appended to" if mode == "append" else "Wrote"
    return f"{action} '{rel(target)}' ({len(content)} characters)."


@mcp.tool()
def create_directory(path: str) -> str:
    """
    Create a new directory (including any missing parent directories).

    Args:
        path: Relative path for the new directory within the root directory.
    """
    target = safe_resolve(path)
    if target.exists():
        return f"'{path}' already exists."
    target.mkdir(parents=True)
    return f"Created directory '{rel(target)}'."


@mcp.tool()
def delete_file(path: str) -> str:
    """
    Delete a single file.

    Args:
        path: Relative path to the file within the root directory.
    """
    target = safe_resolve(path)
    if not target.exists():
        return f"Error: '{path}' does not exist."
    if target.is_dir():
        return f"Error: '{path}' is a directory. Use delete_directory instead."
    target.unlink()
    return f"Deleted file '{path}'."


@mcp.tool()
def delete_directory(path: str, recursive: bool = False) -> str:
    """
    Delete a directory. Fails if it isn't empty unless recursive=True.

    Args:
        path: Relative path to the directory within the root directory.
        recursive: If True, deletes the directory and everything inside it.
    """
    target = safe_resolve(path)
    if target == ROOT_DIR:
        return "Error: refusing to delete the root directory itself."
    if not target.exists():
        return f"Error: '{path}' does not exist."
    if not target.is_dir():
        return f"Error: '{path}' is not a directory."

    if recursive:
        shutil.rmtree(target)
        return f"Deleted directory '{path}' and all its contents."
    else:
        try:
            target.rmdir()
            return f"Deleted empty directory '{path}'."
        except OSError:
            return f"Error: '{path}' is not empty. Pass recursive=True to delete it anyway."


@mcp.tool()
def move_file(source: str, destination: str) -> str:
    """
    Move or rename a file or directory.

    Args:
        source: Relative path of the item to move.
        destination: Relative path of the new location/name.
    """
    src = safe_resolve(source)
    dst = safe_resolve(destination)
    if not src.exists():
        return f"Error: '{source}' does not exist."
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Moved '{source}' to '{destination}'."


@mcp.tool()
def get_file_info(path: str) -> str:
    """
    Get metadata about a file or directory (size, type, timestamps).

    Args:
        path: Relative path within the root directory.
    """
    target = safe_resolve(path)
    if not target.exists():
        return f"Error: '{path}' does not exist."

    stat = target.stat()
    kind = "directory" if target.is_dir() else "file"
    modified = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    created = datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds")

    lines = [
        f"Path: {path}",
        f"Type: {kind}",
        f"Modified: {modified}",
        f"Created: {created}",
    ]
    if target.is_file():
        lines.append(f"Size: {stat.st_size} bytes")
    return "\n".join(lines)


@mcp.tool()
def search_files(pattern: str, path: str = ".") -> str:
    """
    Recursively search for files whose name matches a glob pattern (e.g. '*.py', 'report*.txt').

    Args:
        pattern: Glob-style filename pattern to match.
        path: Relative directory to search within. Defaults to the root.
    """
    start = safe_resolve(path)
    if not start.exists() or not start.is_dir():
        return f"Error: '{path}' is not a valid directory."

    matches = []
    for dirpath, _dirnames, filenames in os.walk(start):
        for name in filenames:
            if fnmatch.fnmatch(name, pattern):
                full = Path(dirpath) / name
                matches.append(rel(full))

    if not matches:
        return f"No files matching '{pattern}' found under '{path}'."
    return "\n".join(sorted(matches))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Starting local-filesystem MCP server, rooted at: {ROOT_DIR}", file=sys.stderr)
    mcp.run(transport="stdio")
