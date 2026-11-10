"""Start one launcher-managed workbench for this installation on a loopback port."""

import asyncio
import errno
import json
import msvcrt
import os
import secrets
import socket
import sys
from pathlib import Path

import uvicorn


async def serve(listener: socket.socket, stop_request: Path) -> None:
    server = uvicorn.Server(uvicorn.Config("main:app", access_log=False, workers=1))

    async def watch_for_stop() -> None:
        while not stop_request.exists():
            await asyncio.sleep(0.2)
        server.should_exit = True

    watcher = asyncio.create_task(watch_for_stop())
    try:
        await server.serve(sockets=[listener])
    finally:
        watcher.cancel()


def main(preferred_port: int) -> int:
    runtime = Path(__file__).resolve().parent / "tmp" / "launcher"
    runtime.mkdir(parents=True, exist_ok=True)
    endpoint = runtime / "workbench-endpoint.json"
    with (runtime / "workbench.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno != errno.EACCES:
                raise
            return 3  # Another launcher is starting or running this installation.

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", preferred_port))
            except OSError as exc:
                if exc.winerror not in (10048, 10013):  # Address in use or reserved by Windows.
                    raise
                listener.bind(("127.0.0.1", 0))

            port = listener.getsockname()[1]
            instance = secrets.token_hex(16)
            stop_request = runtime / f"stop-{instance}.request"
            os.environ["RVC_SERVE_FRONTEND"] = "true"
            os.environ["RVC_LAUNCHER_INSTANCE"] = instance
            pending = endpoint.with_suffix(".tmp")
            pending.write_text(json.dumps({"port": port, "instance": instance, "stopSupported": True}), encoding="utf-8")
            pending.replace(endpoint)
            print(f"Workbench address: http://127.0.0.1:{port}/rvc", flush=True)
            try:
                asyncio.run(serve(listener, stop_request))
            finally:
                stop_request.unlink(missing_ok=True)
                endpoint.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))
