"""Run the receiver service: ``python -m whmi.receiver --config /etc/whmi/receiver.json``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from ..link.credentials import CredentialStore
from .config import DeviceConfig
from .service import Receiver


async def _main(config: DeviceConfig) -> None:
    if not config.credentials_file.exists():
        raise SystemExit(f"no credentials at {config.credentials_file}; run 'whmi receiver init' first")
    receiver = Receiver(config, CredentialStore.load(config.credentials_file))
    await receiver.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    logging.info("shutting down")
    await receiver.stop()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="whmi-receiver", description="WHMI Receiver service")
    ap.add_argument("--config", required=True, help="receiver device config (JSON)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    asyncio.run(_main(DeviceConfig.load(args.config)))


if __name__ == "__main__":
    main()
