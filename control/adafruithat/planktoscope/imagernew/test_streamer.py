import functools

import picamera2
from picamera2 import encoders, outputs

from planktoscope.imagernew import picam_streamer

picam2 = picamera2.Picamera2()
picam2.configure(picam2.create_video_configuration(main={"size": (640, 480)}))
streaming_output = picam_streamer.StreamingOutput()
picam2.start_recording(encoders.MJPEGEncoder(), outputs.FileOutput(streaming_output))

try:
    address = ("", 8000)
    refresh_delay = 1 / 15
    handler = functools.partial(picam_streamer.StreamingHandler, refresh_delay, streaming_output)
    server = picam_streamer.StreamingServer(address, handler)
    server.serve_forever()
finally:
    picam2.stop_recording()
