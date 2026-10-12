#!/usr/bin/env python3
"""Keep curated KDE snapshots separate from the desktop's writable configs."""

import argparse
from datetime import datetime, timezone
import difflib
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile


APPLETS = "plasma-org.kde.plasma.desktop-appletsrc"
LOCAL_FILES = ("ksmserverrc", "plasmanotifyrc")

# The snapshot file list selects what we manage. These exclusions select which
# parts remain local. Match raw KConfig names; never unescape values as INI.
LOCAL_GROUPS = {
    "kwinrc": (r"^\[Tiling\]",),
    "plasmashellrc": (r"^\[Updates\]",),
    "plasmanotifyrc": (r"^\[Applications\]",),
    APPLETS: (r"^\[ScreenMapping\]", r"\[ConfigDialog\]$"),
}
LOCAL_KEYS = {
    "kwinrc": (r"^Id_\d+$",),
    "kdeglobals": (r"^ColorSchemeHash$",),
    "dolphinrc": (r"^(ViewPropsTimestamp|Version)$",),
    "katerc": (r"^PinnedDocuments$",),
    "katevirc": (r"^Macro (Completions|Contents|Registers)$",),
    "kglobalshortcutsrc": (r"^switch-to-activity-",),
    APPLETS: (r"^(Image|SlidePaths|currentTab|activityId|lastScreen|knownItems)$",
              r"^ItemGeometries"),
}


def sections(text):
    """Split KConfig without changing escapes, nested groups, or comments."""
    result = [("", [])]
    for line in text.splitlines():
        if line.startswith("[") and line.endswith("]"):
            result.append((line, []))
        else:
            result[-1][1].append(line)
    return result


def entry(line):
    if not line or line.startswith(("#", ";")):
        return None
    key, separator, value = line.partition("=")
    if not separator:
        raise ValueError(f"unsupported KConfig line: {line!r}")
    return key, value


def key_name(key):
    # Options such as [$e] and [$i] belong to the entry, not its name. A
    # locale suffix such as [en_US] remains part of the key's identity.
    return re.sub(r"(?:\[\$[^]]*\])+$", "", key)


def sanitize(name, text):
    if name == "kdedefaults/package":
        return text
    groups = {}
    for group, lines in sections(text):
        if any(re.search(pattern, group) for pattern in LOCAL_GROUPS.get(name, ())):
            continue
        for line in lines:
            item = entry(line)
            if item is None:
                continue
            key, value = item
            base_key = key_name(key)
            if any(re.search(pattern, base_key) for pattern in LOCAL_KEYS.get(name, ())):
                continue
            # KDE reads the last occurrence unless an earlier value is
            # immutable. Deduplicate before sorting, keeping its options.
            keys = groups.setdefault(group, {})
            previous = keys.get(base_key)
            if previous is None or "[$i]" not in previous[0]:
                keys[base_key] = (key, value)
    blocks = []
    for group, keys in sorted(groups.items()):
        lines = ([group] if group else []) + [f"{key}={value}" for _, (key, value) in sorted(keys.items())]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def merge(live, snapshot):
    """Set snapshot keys, retaining unmentioned local keys and groups."""
    updates = {}
    for group, lines in sections(snapshot):
        for line in lines:
            item = entry(line)
            if item is not None:
                updates.setdefault(group, {})[key_name(item[0])] = item
    output = []
    blocks = sections(live)
    last_block = {group: index for index, (group, _) in enumerate(blocks)}
    seen = {}
    for index, (group, lines) in enumerate(blocks):
        if group:
            output.append(group)
        pending = updates.get(group, {})
        present = seen.setdefault(group, set())
        body = []
        for line in lines:
            item = entry(line)
            if item is not None and key_name(item[0]) in pending:
                name = key_name(item[0])
                key, value = pending[name]
                line = f"{key}={value}"
                present.add(name)
            body.append(line)
        # Insert new keys inside their group, before its trailing blank lines.
        tail = []
        while body and not body[-1]:
            tail.append(body.pop())
        output.extend(body)
        if index == last_block[group]:
            output.extend(f"{key}={value}" for name, (key, value) in pending.items() if name not in present)
        output.extend(tail)
    for group, keys in updates.items():
        if group in last_block:
            continue
        if output and output[-1]:
            output.append("")
        if group:
            output.append(group)
        output.extend(f"{key}={value}" for key, value in keys.values())
    return "\n".join(output) + ("\n" if output else "")


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Backups:
    def __init__(self, state):
        self.state = state
        self.directory = None

    def save(self, path, relative):
        if not path.exists():
            return
        if self.directory is None:
            root = self.state / "dotfiles/kde-backups"
            root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
            self.directory = Path(tempfile.mkdtemp(prefix=stamp, dir=root))
            print(f"Backups: {self.directory}")
        destination = self.directory / relative
        if destination.exists():
            return  # already saved as part of a folded directory
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_dir():
            shutil.copytree(path, destination, symlinks=True)
        else:
            shutil.copy2(path, destination)


def check_paths(repo, config, files, installing=False):
    """Preflight every target before a command can write any of them."""
    legacy = repo / "os/endeavour/config"
    if config.resolve().is_relative_to(repo):
        raise ValueError(f"{config}: config directory must live outside the repo")
    for relative in files:
        target = config / relative
        for prefix in [*reversed(target.parents), target]:
            if prefix == config or config not in prefix.parents:
                continue
            if prefix.is_symlink():
                expected = legacy / prefix.relative_to(config)
                if not installing or prefix.resolve() != expected.resolve():
                    raise ValueError(f"{prefix}: symlink is not writable; run install to migrate legacy KDE links")
        if target.resolve().is_relative_to(repo):
            if not installing:
                raise ValueError(f"{target}: still points into the repo; run install first")
        if target.exists() and not target.is_file():
            raise ValueError(f"{target}: expected a config file")


def install(repo, config, files, backups, dry_run):
    files = dict(files)
    # Older installs also linked session/discovery files which contain no
    # preferences. Detach any surviving links without creating snapshots.
    for name in LOCAL_FILES:
        if name not in files and (config / name).is_symlink():
            files[name] = None
    check_paths(repo, config, files, installing=True)
    # Stow may have folded a whole directory, including untracked local files.
    # Detach that directory first, preserving everything, then its leaf links.
    directories = sorted({parent for relative in files for parent in (config / relative).parents
                          if parent != config and config in parent.parents}, key=lambda p: len(p.parts))
    for directory in directories:
        if not directory.is_symlink():
            continue
        print(f"Detach directory: {directory}")
        if dry_run:
            continue
        backups.save(directory, directory.relative_to(config))
        link = os.readlink(directory)
        temporary = Path(tempfile.mkdtemp(prefix=f".{directory.name}.", dir=directory.parent))
        try:
            if directory.exists():
                shutil.copytree(directory, temporary, symlinks=True, dirs_exist_ok=True)
            else:
                print(f"Warning: {directory}: legacy target is missing; seeding snapshots", file=sys.stderr)
            directory.unlink()
            try:
                os.replace(temporary, directory)
            except OSError:
                directory.symlink_to(link)
                raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    for relative, snapshot in files.items():
        target = config / relative
        if target.is_symlink():
            print(f"Detach file: {target}")
            if dry_run:
                continue
            if target.exists():
                data = target.read_bytes()
                backups.save(target, relative)
            else:
                print(f"Warning: {target}: legacy target is missing; seeding snapshot", file=sys.stderr)
                data = snapshot.read_bytes() if snapshot else b""
            atomic_write(target, data)
        elif not target.exists() and snapshot is not None:
            print(f"Seed: {target}")
            if not dry_run:
                atomic_write(target, snapshot.read_bytes())


def show_diff(before, after, name):
    sys.stdout.writelines(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"before/{name}", tofile=f"after/{name}",
    ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "apply", "capture"))
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dry-run", action="store_true", help="preview install/apply without writing")
    parser.add_argument("--write", action="store_true", help="save capture's preview into the snapshots")
    args = parser.parse_args()
    if args.write and (args.command != "capture" or args.dry_run):
        parser.error("--write is only valid for capture, without --dry-run")
    repo = args.repo.resolve()
    source = repo / "os/endeavour/kde/config"
    files = {path.relative_to(source).as_posix(): path for path in sorted(source.rglob("*")) if path.is_file()}
    if not files:
        parser.error(f"no KDE snapshots found in {source}")
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").absolute()
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state").absolute()
    backups = Backups(state)
    try:
        if args.command == "install":
            install(repo, config, files, backups, args.dry_run)
            return 0
        if args.command == "apply":
            check_paths(repo, config, files)
        changes = []
        # Read and validate all inputs before writing the first output.
        for relative, snapshot in files.items():
            target = config / relative
            before = snapshot.read_text(encoding="utf-8")
            if args.command == "capture":
                after = sanitize(relative, target.read_text(encoding="utf-8"))
                destination = snapshot
            else:
                settings = sanitize(relative, before)
                before = target.read_text(encoding="utf-8") if target.exists() else ""
                after = settings if relative == "kdedefaults/package" else merge(before, settings)
                destination = target
            if before != after:
                changes.append((relative, destination, before, after))
        for relative, destination, before, after in changes:
            show_diff(before, after, relative)
            if (args.command == "capture" and args.write) or (args.command == "apply" and not args.dry_run):
                if args.command == "apply":
                    backups.save(destination, relative)
                atomic_write(destination, after.encode("utf-8"))
        if not changes:
            print("No preference changes.")
        elif args.command == "capture" and not args.write:
            print("Preview only. Run capture --write to save these preferences.")
        return 0
    except (OSError, ValueError) as error:
        print(f"kde-config: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
