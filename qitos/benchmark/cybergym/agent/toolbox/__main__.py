"""CLI entry point for the format-aware toolbox.

Usage:
    python3 -m toolbox <domain> <command> [options]

Domains: png, jpeg, zip, pdf, bmp, wav, mutate, binary

Commands per format domain:
    minimal     - Generate a minimal valid carrier (stdout or --output FILE)
    inspect FILE - Parse file structure, output JSON

Mutate commands:
    patch   --file FILE --offset N --hex AA BB ...
    append  --file FILE --hex AA BB ...
    truncate --file FILE --size N

Binary commands:
    hexdump FILE [--offset N] [--length N]
    find    FILE --hex AA BB ...
    slice   FILE --offset N --length N
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _write_output(data: bytes, output_path: str | None) -> None:
    """Write bytes to file or stdout."""
    if output_path:
        Path(output_path).write_bytes(data)
        print(f"Wrote {len(data)} bytes to {output_path}")
    else:
        sys.stdout.buffer.write(data)


def _handle_format_domain(domain: str, command: str, args: argparse.Namespace) -> None:
    """Handle format domain commands (minimal, inspect)."""
    from .formats import png, jpeg, zipfmt, pdf, bmp, wav

    domain_map = {
        "png": png,
        "jpeg": jpeg,
        "jpg": jpeg,
        "zip": zipfmt,
        "pdf": pdf,
        "bmp": bmp,
        "wav": wav,
    }
    mod = domain_map.get(domain)
    if mod is None:
        print(f"Unknown domain: {domain}", file=sys.stderr)
        sys.exit(1)

    if command == "minimal":
        data = mod.minimal()
        _write_output(data, args.output)
    elif command == "inspect":
        if not args.file:
            print("inspect requires --file", file=sys.stderr)
            sys.exit(1)
        result = mod.inspect(args.file)
        print(json.dumps(result, indent=2))
    else:
        print(f"Unknown command for {domain}: {command}", file=sys.stderr)
        sys.exit(1)


def _handle_mutate(command: str, args: argparse.Namespace) -> None:
    """Handle mutation commands."""
    from .mutate import patch_bytes, append_bytes, truncate_bytes

    if command == "patch":
        if not args.file or args.offset is None or not args.hex:
            print("patch requires --file, --offset, --hex", file=sys.stderr)
            sys.exit(1)
        data = Path(args.file).read_bytes()
        hex_vals = [int(h, 16) for h in args.hex]
        result = patch_bytes(data, args.offset, bytes(hex_vals))
        Path(args.file).write_bytes(result)
        print(f"Patched {len(hex_vals)} bytes at offset {args.offset}")
    elif command == "append":
        if not args.file or not args.hex:
            print("append requires --file, --hex", file=sys.stderr)
            sys.exit(1)
        data = Path(args.file).read_bytes()
        hex_vals = [int(h, 16) for h in args.hex]
        result = append_bytes(data, bytes(hex_vals))
        Path(args.file).write_bytes(result)
        print(f"Appended {len(hex_vals)} bytes")
    elif command == "truncate":
        if not args.file or args.size is None:
            print("truncate requires --file, --size", file=sys.stderr)
            sys.exit(1)
        data = Path(args.file).read_bytes()
        result = truncate_bytes(data, args.size)
        Path(args.file).write_bytes(result)
        print(f"Truncated to {args.size} bytes")
    else:
        print(f"Unknown mutate command: {command}", file=sys.stderr)
        sys.exit(1)


def _handle_binary(command: str, args: argparse.Namespace) -> None:
    """Handle binary operation commands."""
    from .binary import hexdump, find_bytes, slice_bytes

    if command == "hexdump":
        if not args.file:
            print("hexdump requires --file", file=sys.stderr)
            sys.exit(1)
        data = Path(args.file).read_bytes()
        offset = args.offset or 0
        length = args.length or 256
        print(hexdump(data, offset, length))
    elif command == "find":
        if not args.file or not args.hex:
            print("find requires --file, --hex", file=sys.stderr)
            sys.exit(1)
        data = Path(args.file).read_bytes()
        pattern = bytes(int(h, 16) for h in args.hex)
        positions = find_bytes(data, pattern)
        if positions:
            print(json.dumps(positions))
        else:
            print("Pattern not found")
    elif command == "slice":
        if not args.file or args.offset is None or args.length is None:
            print("slice requires --file, --offset, --length", file=sys.stderr)
            sys.exit(1)
        data = Path(args.file).read_bytes()
        chunk = slice_bytes(data, args.offset, args.length)
        _write_output(chunk, args.output)
    else:
        print(f"Unknown binary command: {command}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="toolbox",
        description="Format-aware toolbox for PoC generation",
    )
    parser.add_argument("domain", help="Domain: png, jpeg, zip, pdf, bmp, wav, mutate, binary")
    parser.add_argument("command", help="Command: minimal, inspect, patch, append, truncate, hexdump, find, slice")
    parser.add_argument("--file", "-f", help="Input file path")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--offset", type=int, default=None, help="Byte offset")
    parser.add_argument("--length", type=int, default=None, help="Length in bytes")
    parser.add_argument("--size", type=int, default=None, help="Target size for truncate")
    parser.add_argument("--hex", nargs="+", help="Hex byte values (e.g., 89 50 4E)")

    args = parser.parse_args()

    format_domains = {"png", "jpeg", "jpg", "zip", "pdf", "bmp", "wav"}
    if args.domain in format_domains:
        _handle_format_domain(args.domain, args.command, args)
    elif args.domain == "mutate":
        _handle_mutate(args.command, args)
    elif args.domain == "binary":
        _handle_binary(args.command, args)
    else:
        print(f"Unknown domain: {args.domain}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
