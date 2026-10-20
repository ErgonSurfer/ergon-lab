# SPDX-License-Identifier: MIT
"""Offline input commitment; never imports or executes an operator module.

This is not approval, a Python sandbox, index adoption, or a build identity.
The supplied interpreter profile must match the configured embedding profile.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

DOMAIN = b"ergon-trusted-operator-plugin-bundle/regtest/v1\n"
MAX_MODULES, MAX_SOURCE, MAX_CONFIG, MAX_TOTAL = 32, 1024 * 1024, 64 * 1024, 4 * 1024 * 1024


def require(condition):
    if not condition:
        raise ValueError("Operator bundle contract refused")


def metadata(meta, limit):
    require(stat.S_ISREG(meta.st_mode) and meta.st_size <= limit)
    if os.name == "posix":
        require(meta.st_nlink == 1 and meta.st_mode & 0o022 == 0)
    return (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def inspect(root, python_version, python_id, network="regtest"):
    require(network in ("main", "regtest", "ergon-testnet-v1"))
    root = Path(root)
    require(root.is_absolute() and root.resolve(strict=True) == root)
    require(stat.S_ISDIR(root.lstat().st_mode))
    files, total = {}, 0
    for path in root.iterdir():
        require(len(files) < MAX_MODULES + 1)
        name = path.name
        if name == "plugins.toml":
            limit = MAX_CONFIG
        else:
            require(re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*\.py", name) is not None)
            limit = MAX_SOURCE
        before = metadata(path.lstat(), limit)
        with path.open("rb") as file:
            opened = metadata(os.fstat(file.fileno()), limit)
            require(before == opened)
            data = file.read(limit + 1)
        require(opened == metadata(path.lstat(), limit) and len(data) == opened[2])
        data.decode("utf-8", errors="strict")
        require(b"\x00" not in data)
        total += len(data)
        require(total <= MAX_TOTAL)
        files[name] = data
    require("plugins.toml" in files and len(files) >= 2)
    identity = bytearray(f"ergon-trusted-operator-plugin-bundle/{network}/v1\n".encode("ascii"))

    def field(data):
        identity.extend(len(data).to_bytes(8, "little"))
        identity.extend(data)

    field(python_version.encode("utf-8"))
    field(python_id.encode("utf-8"))
    identity.extend(len(files).to_bytes(8, "little"))
    for name, data in sorted(files.items()):
        field(name.encode("utf-8"))
        field(data)
    return hashlib.sha256(identity).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--python-id", required=True)
    parser.add_argument("--network", choices=("main", "regtest", "ergon-testnet-v1"), default="regtest")
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"3\.12\.[0-9]+", args.python_version) is not None)
        require(re.fullmatch(r"[0-9a-f]{64}", args.python_id) is not None)
        print(inspect(args.bundle, args.python_version, args.python_id, args.network))
        return 0
    except (OSError, ValueError):
        # No source/config/path/host metadata in portable diagnostic output.
        print("Operator bundle inspection refused", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
