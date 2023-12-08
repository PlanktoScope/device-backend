from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

import functools

import planktoscope.imagernew.picam_streamer as stream


picam2 = Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
streaming_output = stream.StreamingOutput()
picam2.start_recording(MJPEGEncoder(), FileOutput(streaming_output))

try:
    address = ('', 8000)
    refresh_delay = 1 / 15
    handler = functools.partial(stream.StreamingHandler, refresh_delay, streaming_output)
    server = stream.StreamingServer(address, handler)
    server.serve_forever()
finally:
    picam2.stop_recording()
