import asyncio
import json
import os
import time

MAX_REQUEST = 262144


async def request(socket, payload, timeout=15):
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(socket), limit=MAX_REQUEST), timeout
    )
    try:
        writer.write(json.dumps(payload).encode() + b"\n")
        await writer.drain()
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout))
        if "error" in response:
            raise ValueError(response["error"])
        return response["result"]
    finally:
        writer.close()
        await writer.wait_closed()


async def serve(engine):
    async def handle(reader, writer):
        try:
            payload = json.loads(await asyncio.wait_for(reader.readline(), 5))
            action = payload["action"]
            if action == "ready":
                if payload.get("boot_id") == engine.generation:
                    engine.ready_at = time.monotonic()
                    engine.ready.set()
                result = True
            elif action == "status":
                result = {
                    "commit": engine.state.get("commit"),
                    "running": engine.child is not None and engine.child.returncode is None,
                    "healthy": engine.ready.is_set() and time.monotonic() - engine.ready_at < 4,
                    "operation": engine.state.get("operation"),
                }
                # Destination metadata is local-only and unnecessary in chat/CLI status output.
                if result["operation"]:
                    result["operation"] = {k: v for k, v in result["operation"].items() if k != "target"}
            elif action in {"update", "restart"}:
                target = payload.get("target")
                if target is not None:
                    allowed = {"adapter", "kind", "id", "bot_id", "reply_id", "reply_sequence"}
                    if (
                        not isinstance(target, dict)
                        or set(target) - allowed
                        or any(
                            value is not None and (not isinstance(value, str) or len(value) > 256)
                            for key, value in target.items()
                            if key != "reply_sequence"
                        )
                    ):
                        raise ValueError("Invalid report target")
                    sequence = target.get("reply_sequence")
                    if sequence is not None and (type(sequence) is not int or not 1 <= sequence <= 100):
                        raise ValueError("Invalid reply sequence")
                result = engine.submit(action, target)
            elif action in {"fetch", "log"}:
                if engine.lock.locked() or engine.task and not engine.task.done():
                    raise ValueError("Another operation is running")
                async with engine.lock:
                    if action == "fetch":
                        result = await engine.fetch()
                    else:
                        result = await engine.git(
                            "log", "-5", "--format=%h %s", engine.state["commit"], capture=True
                        )
                        from .state import sanitize

                        result = "\n".join(sanitize(line) for line in result.splitlines())
            elif action == "reports":
                result = engine.state.get("reports", [])[:1]
            elif action == "progress":
                for report in engine.state.get("reports", []):
                    if report["id"] == payload["id"]:
                        page = payload.get("page")
                        if type(page) is not int or not 0 <= page <= 50:
                            raise ValueError("Invalid report page")
                        report["page"] = max(report.get("page", 0), page)
                engine.save()
                result = True
            elif action == "ack":
                engine.state["reports"] = [
                    r for r in engine.state.get("reports", []) if r["id"] != payload["id"]
                ]
                engine.save()
                result = True
            else:
                raise ValueError("Unknown action")
            response = {"result": result}
        except Exception as error:
            response = {"error": str(error) if isinstance(error, ValueError) else type(error).__name__}
        try:
            writer.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    engine.config.socket.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle, path=str(engine.config.socket), limit=MAX_REQUEST)
    os.chmod(engine.config.socket, 0o600)
    return server
