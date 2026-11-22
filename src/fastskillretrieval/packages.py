"""Bounded package IO. Original skill files are never modified or executed."""

import hashlib
import os
import stat
import time
from pathlib import Path

from .contracts import Failure, Limits, serialized
from .skill_syntax import has_native_substitutions


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def replace_snapshot(source: Path, destination: Path) -> None:
    """Atomic rename with a finite retry for transient Windows sharing errors."""
    for attempt in range(4):
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            if attempt == 3 or getattr(error, "winerror", None) not in {5, 32, 33}:
                raise
            time.sleep(0.05 * (attempt + 1))


def safe_path(path: Path) -> Path:
    """Reject symlinks and Windows reparse points in every existing component."""
    path = Path(os.path.abspath(path))
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Failure("policy_denied", "linked_path")
    return path


def relative_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise Failure("policy_denied", "invalid_resource_path")
    path = Path(relative)
    if path.is_absolute() or any(part in {"..", "."} for part in relative.split("/")):
        raise Failure("policy_denied", "resource_outside_package")
    result = safe_path(root / path)
    if not result.is_relative_to(root):
        raise Failure("policy_denied", "resource_outside_package")
    return result


def read_package(
    root: Path, limits: Limits, entry_file: str = "SKILL.md"
) -> tuple[dict[str, bytes], dict[str, str], str]:
    root = safe_path(root)
    if not root.is_dir():
        raise Failure("stale_reference", "package_missing")
    files = {}
    total = 0
    # Walk explicitly so directory reparse points are rejected before descending.
    pending = [root]
    directories = 0
    while pending:
        folder = pending.pop()
        directories += 1
        if directories > limits.package_files:
            raise Failure("limit_exceeded", "package_directories")
        for child in sorted(folder.iterdir()):
            child = safe_path(child)
            if child.is_dir():
                if child.name in {".git", ".venv", "node_modules", ".aws", ".ssh"}:
                    raise Failure("policy_denied", "unrelated_directory")
                pending.append(child)
                if len(pending) > limits.package_files:
                    raise Failure("limit_exceeded", "package_directories")
                continue
            if not child.is_file():
                raise Failure("policy_denied", "non_regular_file")
            if child.name == ".env" or child.name.startswith(".env."):
                raise Failure("policy_denied", "sensitive_file")
            relative = child.relative_to(root).as_posix()
            maximum = limits.skill_bytes if relative == entry_file else limits.package_bytes
            # Read at most the remaining allowance, even if the file grows during read.
            allowance = min(maximum, limits.package_bytes - total)
            with child.open("rb") as stream:
                data = stream.read(allowance + 1)
            if len(data) > allowance or len(files) >= limits.package_files:
                raise Failure("limit_exceeded", "package_size_or_files")
            total += len(data)
            files[relative] = data
    if entry_file not in files:
        raise Failure("stale_reference", "skill_missing")
    try:
        files[entry_file].decode("utf-8")
    except UnicodeDecodeError as error:
        raise Failure("policy_denied", "skill_not_utf8") from error
    manifest = {name: digest(data) for name, data in sorted(files.items())}
    return files, manifest, digest(serialized(manifest).encode())


def supported_instructions(data: bytes) -> None:
    """A body read cannot preserve Claude-specific execution semantics."""
    # Local import keeps the bounded path helpers usable by the definition parser.
    from .capabilities import _simple_frontmatter, skill_requires_native

    source = data.decode("utf-8-sig")
    metadata, body = _simple_frontmatter(source)
    if skill_requires_native(metadata, ""):
        raise Failure("unsupported_integration", "native_frontmatter_requires_invocation")
    if has_native_substitutions(body):
        raise Failure("unsupported_integration", "native_substitution_requires_invocation")
