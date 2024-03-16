import io
import socketserver
import threading
import time
from http import server

import loguru


################################################################################
# Classes for the PiCamera Streaming
################################################################################
class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        super().__init__()
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class StreamingHandler(server.BaseHTTPRequestHandler):
    def __init__(self, delay, output, *args, **kwargs):
        self.delay = delay
        self.output = output
        super().__init__(*args, **kwargs)

    @loguru.logger.catch
    def do_GET(self):
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/stream.mjpg")
            self.end_headers()
        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", 0)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    try:
                        with self.output.condition:
                            self.output.condition.wait()
                            frame = self.output.frame
                    except Exception as e:
                        loguru.logger.exception(f"An exception occured {e}")
                    else:
                        self.wfile.write(b"--FRAME\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", len(frame))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                        time.sleep(self.delay)

            except Exception:
                loguru.logger.info("Removed streaming client")
        else:
            self.send_error(404)
            self.end_headers()


class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True
