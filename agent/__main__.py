"""Run one robot process against the Fleet and Artifact Registry."""

import fcntl
import logging
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

from agent.runtime import Agent
from agent.settings import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    with (settings.state_dir / "agent.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        agent = Agent(settings)

        class MetricsHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/metrics":
                    self.send_error(404)
                    return
                body = agent.metrics()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        stop = Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        with ThreadingHTTPServer(settings.metrics_bind, MetricsHandler) as server:
            metrics = Thread(target=server.serve_forever, daemon=True)
            heartbeat = Thread(target=agent.run_heartbeats, args=(stop,), daemon=True)
            reconcile = Thread(
                target=agent.run_reconciliation, args=(stop,), daemon=True
            )
            agent.log("agent_started", version="0.5.0")
            metrics.start()
            heartbeat.start()
            reconcile.start()
            while not stop.wait(0.5):
                if not heartbeat.is_alive() or not reconcile.is_alive():
                    raise SystemExit("A robot agent worker stopped")
            # Downloads may still be in flight. Each staging chunk was durable.
            server.shutdown()


if __name__ == "__main__":
    main()
