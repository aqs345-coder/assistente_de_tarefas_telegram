import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

    def log_message(self, format, *args):
        logger.info("HTTP %s", format % args)


def start_http_server(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(
        target=server.serve_forever, daemon=True, name="health-server"
    ).start()
    logger.info("Health server escutando na porta %s", port)
    return server


def stop_http_server(server):
    logger.info("Parando health server")
    server.shutdown()
    server.server_close()
