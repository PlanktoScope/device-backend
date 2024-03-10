from loguru import logger

import io
import time

import socketserver
import http.server
from threading import Condition

PAGE = """\
<html>
    <head>
        <title>picamera2 MJPEG streaming demo</title>
    </head>
    <body>
        <h1>Picamera2 MJPEG Streaming Demo</h1>
        <img src="stream.mjpg" width="800" height="600" />
    </body>
</html>
"""

################################################################################
# Classes for the PiCamera Streaming
################################################################################
class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

class StreamingHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, delay, output, *args, **kwargs):
        self.delay = delay
        self.output = output
        super(StreamingHandler, self).__init__(*args, **kwargs)

    @logger.catch
    def do_GET(self):
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html") #stream.mjpg
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", 0)
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header(
                "Content-Type", "multipart/x-mixed-replace; boundary=FRAME"
            )
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                while True:
                    try:
                        with self.output.condition:
                            self.output.condition.wait()
                            frame = self.output.frame
                    except Exception as e:
                        logger.exception(f"An exception occured {e}")
                    else:
                        self.wfile.write(b"--FRAME\r\n")
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", len(frame))
                        self.end_headers()
                        self.wfile.write(frame)
                        self.wfile.write(b"\r\n")
                        time.sleep(self.delay)

            except Exception as e:
                logger.info(f"Removed streaming client {self.client_address}") #FIXME client_address is not defined, remove it?
        else:
            self.send_error(404)
            self.end_headers()


class StreamingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True