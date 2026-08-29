#!/usr/bin/env python3
#
# Puts this repo's non-Steam shortcuts back into Steam, with their artwork.
#
# Everything a Steam shortcut is lives in two places SteamOS wipes on a
# re-image and no package manager restores: `shortcuts.vdf`, a binary file in
# the logged-in user's `userdata` directory, and a pile of pictures next to it
# named after an id Steam made up. Neither is derivable from anything else on
# the machine, so without this the emulators and the ports come back as a
# library of blank grey tiles you re-add by hand, one dialog at a time.
#
# The table is steam-shortcuts.conf. See its header for the columns.
#
# WHY PYTHON, IN A DIRECTORY OF BASH
#
# `shortcuts.vdf` is binary: length-less null-terminated strings, little-endian
# int32s, and a type byte in front of every key. Bash can shell out to `xxd`
# and splice bytes with `printf`, and the result would be the least reviewable
# file in this repo. The read-modify-write is the whole job here, so it picks
# the language.
#
# STEAM MUST BE CLOSED, AND THIS REFUSES OTHERWISE
#
# Steam holds the shortcut list in memory and writes it out over whatever is on
# disk when it exits. Edit the file while it is running and the change survives
# until the next time you quit Steam, which is the worst possible failure: it
# looks like it worked. So this checks, and stops.
#
# IDEMPOTENT
#
# A shortcut whose name is already in the file is left alone, and artwork
# already downloaded is not fetched again. Re-running it after adding one row
# does one row's work. `--force` re-downloads the art and rewrites the matching
# entries in place; it never touches an entry this table does not name.

import argparse
import os
import shutil
import struct
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONF = Path(__file__).resolve().parent / "steam-shortcuts.conf"
REPO = Path(__file__).resolve().parents[2]

COLUMNS = ["key", "name", "sgdb", "appid", "exe", "args", "desktop", "icon",
           "p", "wide", "hero", "logo", "iconart"]

# Which SteamGridDB directory each art column comes from, and the suffix Steam
# expects on the file. The empty suffix is the wide capsule -- Steam's own
# naming, not a placeholder.
ART = {
    "p":       ("grid", "p"),
    "wide":    ("grid", ""),
    "hero":    ("hero", "_hero"),
    "logo":    ("logo", "_logo"),
    "iconart": ("icon", "_icon"),
}

CDN = "https://cdn2.steamgriddb.com"

# Binary VDF type bytes.
MAP, STR, INT, END = 0x00, 0x01, 0x02, 0x08

# The field order Steam itself writes. Matching it is not required by the
# format, but a file that diffs cleanly against one Steam produced is worth
# the constant.
FIELDS = [
    ("appid", INT), ("AppName", STR), ("Exe", STR), ("StartDir", STR),
    ("icon", STR), ("ShortcutPath", STR), ("LaunchOptions", STR),
    ("IsHidden", INT), ("AllowDesktopConfig", INT), ("AllowOverlay", INT),
    ("OpenVR", INT), ("Devkit", INT), ("DevkitGameID", STR),
    ("DevkitOverrideAppID", INT), ("LastPlayTime", INT),
    ("FlatpakAppID", STR), ("sortas", STR),
]


def die(msg):
    print("ERROR: %s" % msg, file=sys.stderr)
    raise SystemExit(1)


# --- the table ----------------------------------------------------------------

def read_conf():
    if not CONF.is_file():
        die("cannot read %s" % CONF)
    rows = []
    for lineno, line in enumerate(CONF.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [c.strip() for c in line.split("|")]
        if len(parts) != len(COLUMNS):
            die("%s:%d has %d columns, expected %d"
                % (CONF.name, lineno, len(parts), len(COLUMNS)))
        row = dict(zip(COLUMNS, parts))
        try:
            row["appid"] = int(row["appid"])
        except ValueError:
            die("%s:%d has a non-numeric appid: %r" % (CONF.name, lineno, row["appid"]))
        rows.append(row)
    if not rows:
        die("%s has no shortcut rows" % CONF.name)
    return rows


def expand(path):
    """`~` is $HOME and `$REPO` is this checkout. Empty stays empty."""
    if not path:
        return ""
    return os.path.expanduser(path.replace("$REPO", str(REPO)))


# --- where Steam keeps it -----------------------------------------------------

def find_config_dir(wanted):
    """The config directory of the one Steam account on this machine."""
    roots, seen = [], set()
    for candidate in (Path.home() / ".steam/steam",
                      Path.home() / ".local/share/Steam"):
        if candidate.is_dir():
            real = candidate.resolve()
            if real not in seen:
                seen.add(real)
                roots.append(real)
    if not roots:
        die("found no Steam installation (looked in ~/.steam/steam and "
            "~/.local/share/Steam). Run Steam once first.")

    users = []
    for root in roots:
        for d in sorted((root / "userdata").glob("*")):
            # `0` and `anonymous` are Steam's placeholders, not accounts.
            if d.is_dir() and d.name.isdigit() and d.name != "0":
                users.append(d)
    if not users:
        die("found no logged-in Steam account under %s/userdata"
            % roots[0])
    if wanted:
        users = [u for u in users if u.name == wanted]
        if not users:
            die("no Steam account with id %s" % wanted)
    if len(users) > 1:
        die("several Steam accounts here (%s). Pick one with --user."
            % ", ".join(u.name for u in users))
    return users[0] / "config"


def steam_is_running():
    """True if Steam is up, None if we could not tell."""
    if shutil.which("pgrep") is None:
        return None
    for name in ("steam", "steamwebhelper"):
        if subprocess.run(["pgrep", "-x", name],
                          stdout=subprocess.DEVNULL).returncode == 0:
            return True
    return False


# --- binary vdf ---------------------------------------------------------------

def read_cstr(data, pos):
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def read_map(data, pos, out):
    """Consume a map body, including its terminating 0x08."""
    while True:
        kind = data[pos]
        pos += 1
        if kind == END:
            return pos
        key, pos = read_cstr(data, pos)
        if kind == STR:
            out[key], pos = read_cstr(data, pos)
        elif kind == INT:
            out[key] = struct.unpack("<I", data[pos:pos + 4])[0]
            pos += 4
        elif kind == MAP:
            sub = {}
            pos = read_map(data, pos, sub)
            out[key] = sub
        else:
            raise ValueError("unknown vdf type 0x%02x at byte %d" % (kind, pos - 1))


def parse(data):
    """(bytes before the first entry, [(raw entry bytes, its fields)])

    Entries are kept as their original bytes rather than re-serialised. Any
    field Steam writes that this script has never heard of then survives a
    rewrite untouched, which is the whole point -- the format grows.
    """
    if data[0] != MAP:
        raise ValueError("does not start with a map")
    _, pos = read_cstr(data, 1)
    head_end = pos
    entries = []
    while data[pos] == MAP:
        start = pos
        pos += 1
        _, pos = read_cstr(data, pos)          # the entry's index, discarded
        fields = {}
        pos = read_map(data, pos, fields)
        entries.append((data[start:pos], fields))
    if data[pos:] != b"\x08\x08":
        raise ValueError("trailing bytes are %r, expected the two 0x08 "
                         "terminators" % data[pos:])
    return data[:head_end], entries


def encode_entry(index, fields, tags):
    out = b"\x00" + str(index).encode() + b"\x00"
    for key, kind in FIELDS:
        value = fields[key]
        if kind == STR:
            out += b"\x01" + key.encode() + b"\x00" + str(value).encode() + b"\x00"
        else:
            out += b"\x02" + key.encode() + b"\x00" + struct.pack("<I", value & 0xFFFFFFFF)
    out += b"\x00tags\x00"
    for key, value in (tags or {}).items():
        out += b"\x01" + str(key).encode() + b"\x00" + str(value).encode() + b"\x00"
    out += b"\x08"                             # end of tags
    return out + b"\x08"


def renumber(raw, index):
    """Rewrite an untouched entry's index so the file stays 0..n-1."""
    end = raw.index(b"\x00", 1)
    return b"\x00" + str(index).encode() + raw[end:]


# --- artwork ------------------------------------------------------------------

MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF", b"\x00\x00\x01\x00")


def download(url, dest, dry_run):
    if dry_run:
        print("    would fetch %s" % dest.name)
        return True
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "dotfiles"})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as exc:
        print("    FAILED %s: %s" % (dest.name, exc), file=sys.stderr)
        return False
    if not body.startswith(MAGIC):
        # A deleted asset serves SteamGridDB's HTML 404 with a 200, so length
        # and status both look fine. The magic number is what catches it.
        print("    FAILED %s: not an image (%d bytes)" % (dest.name, len(body)),
              file=sys.stderr)
        return False
    tmp.write_bytes(body)
    tmp.replace(dest)
    return True


def sync_art(row, grid, force, dry_run):
    """Fetch this row's missing artwork. Returns the icon path, if any."""
    icon = ""
    for column, (kind, suffix) in ART.items():
        name = row[column]
        if not name:
            continue
        dest = grid / ("%d%s%s" % (row["appid"], suffix, Path(name).suffix))
        # Steam picks the art up by appid and suffix whatever the extension is,
        # so a .jpg already sitting where this wants to put a .png counts as
        # present -- and, on --force, has to go, or Steam sees both.
        existing = [p for p in grid.glob("%d%s.*" % (row["appid"], suffix))
                    if not p.name.endswith(".part")]
        if column == "iconart":
            # The one already there, if the download is about to be skipped:
            # the vdf points at a path, and pointing it at an extension that is
            # not on disk shows Steam's default icon and no error.
            icon = str(existing[0] if existing and not force else dest)
        if existing and not force:
            continue
        if download("%s/%s/%s" % (CDN, kind, name), dest, dry_run) and not dry_run:
            print("    %s" % dest.name)
            for stale in existing:
                if stale != dest:
                    stale.unlink()
    return icon


# --- the work -----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Restore this repo's non-Steam shortcuts and their artwork.")
    ap.add_argument("keys", nargs="*", metavar="key",
                    help="limit the run to these rows (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="re-download artwork and rewrite matching entries")
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would change, write nothing")
    ap.add_argument("--user", metavar="ID",
                    help="Steam account id, if this machine has more than one")
    args = ap.parse_args()

    rows = read_conf()
    known = [r["key"] for r in rows]
    if args.keys:
        unknown = [k for k in args.keys if k not in known]
        if unknown:
            die("no such shortcut: %s (have: %s)"
                % (", ".join(unknown), " ".join(known)))
        rows = [r for r in rows if r["key"] in args.keys]

    running = steam_is_running()
    if running and not args.dry_run:
        die("Steam is running. It rewrites shortcuts.vdf from memory when it "
            "exits, so anything written now would be lost. Close Steam and "
            "run this again.")
    if running is None:
        print("WARNING: no pgrep here, so whether Steam is running is unknown. "
              "It must be closed.", file=sys.stderr)

    config = find_config_dir(args.user)
    grid = config / "grid"
    vdf = config / "shortcuts.vdf"
    if not args.dry_run:
        grid.mkdir(parents=True, exist_ok=True)
    print("account: %s" % config.parent.name)

    if vdf.is_file():
        data = vdf.read_bytes()
        try:
            head, entries = parse(data)
        except (ValueError, IndexError) as exc:
            die("%s is not a shortcuts file this understands (%s). Refusing to "
                "overwrite it." % (vdf, exc))
    else:
        # Steam reads a file it never wrote, so a machine where no shortcut has
        # ever been added is not a special case.
        head, entries = b"\x00shortcuts\x00", []
        print("no shortcuts.vdf yet; starting one")

    have = {fields.get("AppName"): i for i, (_, fields) in enumerate(entries)}
    added, replaced, skipped, missing = [], [], [], []

    for row in rows:
        exe = expand(row["exe"])
        print("%s (%s)" % (row["name"], row["key"]))
        if not Path(exe).exists():
            # Not fatal. Every target here is installed by something else --
            # packages.sh, game-ports/install.sh, EmuDeck -- and a restore
            # usually runs before all of them have caught up.
            #
            # This only catches the emulators, which point straight at a binary.
            # The ports point at launch.sh, which exists as soon as this repo
            # does, so their rows are written whether or not the appimage is
            # there yet. That is the better failure: the shortcut is ready for
            # the install, and launch.sh says plainly what is missing if you
            # start one early.
            print("    not installed yet: %s" % exe)
            missing.append(row["name"])
            continue

        icon = sync_art(row, grid, args.force, args.dry_run)
        if row["icon"] != "grid":
            icon = expand(row["icon"])
        elif not icon:
            print("    no iconart column, so `icon = grid` has nothing to point at")

        # What a rewrite must not throw away. Everything else in an entry is
        # this table's to state, but playtime and a favourite star are the
        # user's, recorded by Steam and recoverable from nowhere.
        old = entries[have[row["name"]]][1] if row["name"] in have else {}
        if row["name"] in have:
            if not args.force:
                print("    already in Steam")
                skipped.append(row["name"])
                continue
            replaced.append(row["name"])
        else:
            added.append(row["name"])

        fields = {
            "appid": row["appid"],
            "AppName": row["name"],
            "Exe": '"%s"' % exe,
            "StartDir": "%s/" % os.path.dirname(exe),
            "icon": icon,
            "ShortcutPath": expand(row["desktop"]),
            "LaunchOptions": row["args"],
            "IsHidden": 0, "AllowDesktopConfig": 1, "AllowOverlay": 1,
            "OpenVR": 0, "Devkit": 0, "DevkitGameID": "",
            "DevkitOverrideAppID": 0,
            "LastPlayTime": old.get("LastPlayTime", 0),
            "FlatpakAppID": "", "sortas": "",
        }
        # The index is a placeholder; renumber() assigns the real one at write
        # time, once it is known how many entries survived.
        entry = (encode_entry(0, fields, old.get("tags")), fields)
        if row["name"] in have:
            entries[have[row["name"]]] = entry
            print("    rewritten")
        else:
            have[row["name"]] = len(entries)
            entries.append(entry)
            print("    added")

    if not (added or replaced):
        print("\nnothing to write.")
        return 0

    out = head + b"".join(renumber(raw, i) for i, (raw, _) in enumerate(entries))
    out += b"\x08\x08"
    # Read it back before it lands. A file Steam cannot parse is one it silently
    # replaces with an empty library, taking every shortcut on the machine with
    # it -- including the ones this table does not know about.
    try:
        parse(out)
    except (ValueError, IndexError) as exc:
        die("built a shortcuts file that will not parse (%s). Nothing written." % exc)

    if args.dry_run:
        print("\ndry run: would write %d entries to %s" % (len(entries), vdf))
        return 0

    if vdf.is_file():
        backup = vdf.with_suffix(".vdf.bak")
        shutil.copy2(vdf, backup)
        print("\nbacked up %s" % backup.name)
    tmp = vdf.with_suffix(".vdf.new")
    tmp.write_bytes(out)
    tmp.replace(vdf)

    print("wrote %d entries to %s" % (len(entries), vdf))
    for label, names in (("added", added), ("rewritten", replaced),
                         ("left alone", skipped), ("not installed", missing)):
        if names:
            print("  %-14s %s" % (label + ":", ", ".join(names)))
    print("\nStart Steam to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
