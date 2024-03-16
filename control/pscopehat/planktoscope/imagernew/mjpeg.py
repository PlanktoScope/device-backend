"""mjpeg provides an HTTP server to serve an MJPEG stream."""

import functools
import typing
from http import server

import loguru
import typing_extensions


class ByteBufferStream(typing_extensions.Protocol):
    """ByteBufferStream represents a stream of byte buffers where the latest one can be watched."""

    def wait_next(self) -> None:
        """Blocks until a new byte buffer is available on the stream of byte buffers."""

    def get(self) -> typing.Optional[bytes]:
        """Returns the latest byte buffer from the stream of byte buffers."""


class _StreamingHandler(server.BaseHTTPRequestHandler):
    def __init__(self, latest_frame: ByteBufferStream, *args, **kwargs):
        self.latest_frame = latest_frame
        super().__init__(*args, **kwargs)

    @loguru.logger.catch
    # pylint: disable-next=invalid-name
    def do_GET(self):
        """Handle all HTTP GET requests.

        The root path redirects to the MJPEG stream's path, and the MJPEG stream path's serves all
        frames of the stream on a best-effort basis (with frames dropped when the HTTP client can't
        receive frames quickly enough). All other paths return a 404 error.
        """
        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/stream.mjpg")
            self.end_headers()
            return

        if self.path == "/stream.mjpg":
            self._send_mjpeg_header()
            try:
                while True:
                    self.latest_frame.wait_next()
                    frame = self.latest_frame.get()
                    self._send_mjpeg_frame(frame)
            except BrokenPipeError:
                loguru.logger.info("Removed streaming client")
            return

        self.send_error(404)
        self.end_headers()

    @loguru.logger.catch
    def _send_mjpeg_header(self):
        """Send the headers to start an MJPEG stream."""
        self.send_response(200)
        self.send_header("Age", 0)
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _send_mjpeg_frame(self, frame: bytes):
        """Send the next MJPEG frame from the stream."""
        self.wfile.write(b"--FRAME\r\n")
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.end_headers()
        self.wfile.write(frame)
        self.wfile.write(b"\r\n")


class StreamingServer(server.ThreadingHTTPServer):
    """An HTTP server which serves an MJPEG stream.

    The root path redirects to the MJPEG stream's path, and the MJPEG stream path's serves all
    frames of the stream on a best-effort basis (with frames dropped when the HTTP client can't
    receive frames quickly enough). All other paths return a 404 error.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, mjpeg_stream: ByteBufferStream, server_address=("", 8000)):
        """Initialize a server to serve an MJPEG stream at the specified address.

        Args:
            mjpeg_stream: a stream of byte buffers, each representing an MJPEG frame.
            server_address: a tuple of the form `(host, port)` specifying where the server should
              listen.
        """
        super().__init__(server_address, functools.partial(_StreamingHandler, mjpeg_stream))