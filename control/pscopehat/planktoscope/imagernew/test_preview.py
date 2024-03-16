"""test_streamer is a test script to bring up an isolated camera preview stream on port 8000."""

import argparse

import loguru
import picamera2  # type: ignore
from picamera2 import encoders, outputs  # type: ignore

from planktoscope.imagernew import camera, mjpeg, streams


def main() -> None:
    """Run different tests depending on the provided subcommand."""
    parser = argparse.ArgumentParser(
        prog="test_streamer",
        description="Test the camera preview streaming at varying levels of integration",
    )
    parser.set_defaults(func=main_help)
    subparsers = parser.add_subparsers()
    subparsers.add_parser("minimal").set_defaults(func=main_minimal)
    subparsers.add_parser("wrapped").set_defaults(func=main_wrapped)
    args = parser.parse_args()
    args.func(args)


def main_help(_) -> None:
    """Print a help message."""
    print("You must specify a subcommand! Re-run this command with the --help flag for details.")


def main_minimal(_) -> None:
    """Test the camera and MJPEG streamer without planktoscope-specific hardware abstractions."""
    loguru.logger.info("Starting minimal streaming test...")
    cam = picamera2.Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (640, 480)}))
    preview_stream = streams.LatestByteBuffer()

    try:
        cam.start_recording(encoders.MJPEGEncoder(), outputs.FileOutput(preview_stream))
        server = mjpeg.StreamingServer(preview_stream, ("", 8000))
        server.serve_forever()
    finally:
        cam.stop_recording()
        cam.close()


def main_wrapped(_) -> None:
    """Test the camera and MJPEG streamer with the basic thread-safe hardware abstraction."""
    loguru.logger.info("Starting wrapped streaming test...")
    preview_stream = streams.LatestByteBuffer()
    cam = camera.PiCamera(preview_stream)

    try:
        cam.start()
        server = mjpeg.StreamingServer(preview_stream, ("", 8000))
        server.serve_forever()
    finally:
        cam.stop()
        cam.close()


if __name__ == "__main__":
    main()
