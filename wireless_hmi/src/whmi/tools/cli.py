"""``whmi``: command-line engineering tool (the backend WHMI Studio drives).

Examples::

    whmi new line1.whp --name Line1
    whmi validate line1.whp
    whmi build line1.whp -o line1.whc
    whmi receiver init --data-dir ./rx-data --id RX1
    whmi receiver run --config ./rx-data/receiver.json
    whmi download line1.whp --host 192.168.50.1 --client-id studio --key-file studio.key
    whmi monitor Tank_Level Pump_Run --host 192.168.50.1 --client-id panel1 --key-file panel1.key
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from ..common.codes import ClientKind, Role, Status
from ..common.project import CompiledProject, Project, ProjectError
from ..link.client import LinkClient
from ..link.credentials import CredentialStore
from ..link.crypto import parse_key
from ..link.frames import DEFAULT_PORT


def _print_issues(issues) -> None:
    for i in issues:
        print(f"  {i}", file=sys.stderr)


def _load_compiled(path: str) -> CompiledProject:
    p = Path(path)
    if p.suffix == ".whc":
        return CompiledProject.load(p)
    compiled = Project.load(p).build()
    _print_issues(compiled.warnings)
    return compiled


# ---------------------------------------------------------------------------- project commands


def cmd_new(args) -> int:
    path = Path(args.file)
    if path.exists() and not args.force:
        print(f"{path} exists (use --force)", file=sys.stderr)
        return 1
    project = Project(name=args.name)
    project.receiver.slots = {1: "DI8", 2: "DO8", 3: "AI4", 4: "AO2"}
    project.save(path)
    print(f"created {path}")
    return 0


def cmd_validate(args) -> int:
    issues = Project.load(args.file).validate()
    _print_issues(issues)
    errors = sum(1 for i in issues if i.severity == "error")
    print(f"{args.file}: {errors} error(s), {len(issues) - errors} warning(s)")
    return 1 if errors else 0


def cmd_build(args) -> int:
    try:
        compiled = Project.load(args.file).build()
    except ProjectError as e:
        _print_issues(e.issues)
        print("build failed", file=sys.stderr)
        return 1
    _print_issues(compiled.warnings)
    out = Path(args.output or Path(args.file).with_suffix(".whc"))
    compiled.save(out)
    print(f"built {out}: {len(compiled.project.tags)} tags, sha256 {compiled.hash_hex}")
    return 0


def cmd_tags(args) -> int:
    compiled = _load_compiled(args.file)
    print(f"{'ID':>5}  {'NAME':<36} {'TYPE':<7} {'SOURCE':<7} {'ACCESS':<10} ADDRESS")
    for t in compiled.project.tags:
        addr = f"{t.connection}:{t.address}" if t.connection else t.address
        print(f"{t.tag_id:>5}  {t.name:<36} {t.data_type.name:<7} {t.source:<7} {t.access:<10} {addr}")
    return 0


def cmd_keygen(args) -> int:
    print(os.urandom(32).hex())
    return 0


# ---------------------------------------------------------------------------- receiver commands


def cmd_receiver_init(args) -> int:
    from ..receiver.config import DeviceConfig

    data = Path(args.data_dir).resolve()
    data.mkdir(parents=True, exist_ok=True)
    cfg = DeviceConfig(receiver_id=args.id, data_dir=str(data), listen_port=args.port, io_driver=args.io)
    cfg_path = data / "receiver.json"
    if cfg_path.exists() and not args.force:
        print(f"{cfg_path} exists (use --force)", file=sys.stderr)
        return 1
    cfg.save(cfg_path)
    store = CredentialStore()
    for spec in args.client or ["studio:engineer", "panel1:operator"]:
        cid, _, role = spec.partition(":")
        cred = store.create(cid, role or "operator")
        key_file = data / f"{cid}.key"
        key_file.write_text(cred.key.hex() + "\n")
        os.chmod(key_file, 0o600)
        print(f"client {cid:<12} role {cred.role.name.lower():<9} key -> {key_file}")
    store.save(cfg.credentials_file)
    print(f"receiver config -> {cfg_path}")
    return 0


def cmd_receiver_run(args) -> int:
    from ..receiver.__main__ import main

    main(["--config", args.config, "--log-level", args.log_level])
    return 0


# ---------------------------------------------------------------------------- online commands


def _key(args) -> bytes:
    if args.key:
        return parse_key(args.key)
    if args.key_file:
        return parse_key(Path(args.key_file).read_text())
    env = os.environ.get("WHMI_KEY")
    if env:
        return parse_key(env)
    raise SystemExit("no key given (use --key, --key-file or WHMI_KEY)")


def _client(args, kind: ClientKind = ClientKind.TOOL) -> LinkClient:
    return LinkClient(args.host, args.client_id, _key(args), port=args.port, kind=kind)


async def _download(args) -> int:
    compiled = _load_compiled(args.file)
    async with _client(args, ClientKind.STUDIO) as c:
        assert c.session is not None
        if c.session.role < Role.ENGINEER:
            print(f"client {args.client_id} has role {c.session.role.name}; download needs ENGINEER",
                  file=sys.stderr)
            return 1
        if c.session.project_hash == compiled.hash and not args.force:
            print("receiver already runs this project (use --force to re-download)")
            return 0
        msg = await c.download_project(compiled.data)
        print(f"download OK: {msg}")
    return 0


async def _resolve(c: LinkClient, names: list[str]) -> tuple[CompiledProject, list]:
    project = CompiledProject.from_bytes(await c.fetch_project())
    tags = []
    for n in names:
        t = project.tag(n)
        if t is None:
            raise SystemExit(f"unknown tag {n!r}")
        tags.append(t)
    return project, tags


def _fmt(tag, tv) -> str:
    q = "" if tv.quality.is_good else f"  [{tv.quality.name}]"
    unit = f" {tag.units}" if tag.units else ""
    return f"{tag.name} = {tv.value!r}{unit}{q}"


async def _read(args) -> int:
    async with _client(args) as c:
        _, tags = await _resolve(c, args.tags)
        for tag, tv in zip(tags, await c.read([t.tag_id for t in tags])):
            print(_fmt(tag, tv))
    return 0


async def _write(args) -> int:
    async with _client(args) as c:
        _, [tag] = await _resolve(c, [args.tag])
        try:
            value = json.loads(args.value)
        except ValueError:
            value = args.value  # bare text for STRING tags
        [(_, status)] = await c.write([(tag.tag_id, tag.data_type, value)])
        print(f"{tag.name} <- {value!r}: {status.name}")
        return 0 if status is Status.OK else 1


async def _monitor(args) -> int:
    async with _client(args) as c:
        project, tags = await _resolve(c, args.tags)
        if not tags:
            tags = list(project.project.tags)
        by_id = {t.tag_id: t for t in tags}

        def on_data(values):
            for tv in values:
                if tv.tag_id in by_id:
                    print(_fmt(by_id[tv.tag_id], tv), flush=True)

        c.on_data = on_data
        await c.subscribe(list(by_id), args.interval)
        print(f"monitoring {len(by_id)} tag(s) on {c.receiver_id}; Ctrl+C to stop", file=sys.stderr)
        await c.wait_closed()
    return 0


async def _info(args) -> int:
    async with _client(args) as c:
        assert c.session is not None
        h, size, name = await c.project_info()
        print(f"receiver   : {c.receiver_id}")
        print(f"role       : {c.session.role.name.lower()}")
        print(f"heartbeat  : {c.session.heartbeat_ms} ms, watchdog {c.session.watchdog_ms} ms")
        print(f"project    : {name or '(none)'} {size} bytes sha256 {h.hex() if size else '-'}")
    return 0


# ---------------------------------------------------------------------------- argument parsing


def _online(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--client-id", required=True)
    p.add_argument("--key", help="hex key (prefer --key-file or WHMI_KEY)")
    p.add_argument("--key-file")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="whmi", description="WHMI engineering tool")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new", help="create a new project file")
    p.add_argument("file")
    p.add_argument("--name", default="NewProject")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("validate", help="validate a project")
    p.add_argument("file")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("build", help="compile a project to .whc")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("tags", help="list a project's tags with their ids")
    p.add_argument("file")
    p.set_defaults(fn=cmd_tags)

    p = sub.add_parser("keygen", help="generate a random client key")
    p.set_defaults(fn=cmd_keygen)

    rx = sub.add_parser("receiver", help="receiver setup and service").add_subparsers(dest="rxcmd", required=True)
    p = rx.add_parser("init", help="create receiver config and client credentials")
    p.add_argument("--data-dir", required=True)
    p.add_argument("--id", default="WHMI-RX")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--io", default="simulated", help="I/O driver: simulated | simulated-loopback")
    p.add_argument("--client", action="append", help="client_id:role (repeatable)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_receiver_init)
    p = rx.add_parser("run", help="run the receiver service")
    p.add_argument("--config", required=True)
    p.add_argument("--log-level", default="INFO")
    p.set_defaults(fn=cmd_receiver_run)

    for name, fn, helptext in (
        ("download", _download, "build (if needed) and download a project to a receiver"),
        ("read", _read, "read tags"),
        ("write", _write, "write a tag"),
        ("monitor", _monitor, "subscribe to tags and print changes"),
        ("info", _info, "show receiver/session information"),
    ):
        p = sub.add_parser(name, help=helptext)
        _online(p)
        if name == "download":
            p.add_argument("file", help=".whp project or .whc compiled project")
            p.add_argument("--force", action="store_true")
        elif name == "write":
            p.add_argument("tag")
            p.add_argument("value", help="JSON value, e.g. 42, 3.5, true, '\"text\"'")
        elif name in ("read", "monitor"):
            p.add_argument("tags", nargs="*" if name == "monitor" else "+")
            if name == "monitor":
                p.add_argument("--interval", type=int, default=100)
        p.set_defaults(fn=lambda a, f=fn: asyncio.run(f(a)))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        return 130
    except ProjectError as e:
        _print_issues(e.issues)
        return 1


if __name__ == "__main__":
    sys.exit(main())
