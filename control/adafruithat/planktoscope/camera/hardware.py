"""hardware provides basic I/O abstractions for camera hardware."""

import io
import threading
import typing

import libcamera  # type: ignore
import loguru
import picamera2  # type: ignore
import typing_extensions
from picamera2 import encoders, outputs
from readerwriterlock import rwlock


class PiCamera:
    """A thread-safe wrapper around a picamera2-based camera.

    Attributes:
        controls: a [Controls] instance associated with the camera. Uninitialized until the
          `start()` method is called.
    """

    def __init__(
        self,
        preview_output: io.BufferedIOBase,
        preview_size: tuple[int, int] = (640, 480),
        preview_bitrate: typing.Optional[int] = None,
    ) -> None:
        """Set up state needed to initialize the camera, but don't actually start the camera.

        Args:
            preview_output: an image stream which this `PiCamera` instance will write camera preview
              images to.
            preview_size: the width and height (in pixels) of the camera preview.
            preview_bitrate: the bitrate (in bits/sec) of the preview stream; defaults to a bitrate
              for a high-quality stream.
        """
        # Settings
        self._preview_size: tuple[int, int] = preview_size
        self._preview_bitrate: typing.Optional[int] = preview_bitrate

        # I/O
        self._preview_output = preview_output
        self._camera: typing.Optional[picamera2.Picamera2] = None
        self.controls: typing.Optional[Controls] = None

    def open(self) -> None:
        """Start the camera in the background, including output to the preview stream."""
        loguru.logger.debug("Configuring the camera...")
        self._camera = picamera2.Picamera2()

        # We use the `create_still_configuration` to get the best defaults for still images from the
        # "main" stream:
        config = self._camera.create_still_configuration(
            main={"size": self._camera.sensor_resolution},
            lores={"size": self._preview_size},
            # We need at least three buffers to allow the preview to continue receiving frames
            # smoothly from the lores stream while a buffer is reserved for saving an image from
            # the main stream:
            buffer_count=3,
        )
        loguru.logger.debug(f"Camera configuration: {config}")
        self._camera.configure(config)

        self.controls = Controls(self._camera, config["controls"]["FrameDurationLimits"])

        loguru.logger.debug("Starting the camera...")
        self._camera.start_recording(
            # For compatibility with the RPi4 (which must use YUV420 for lores stream output), we
            # cannot use JpegEncoder (which only accepts RGB, not YUV); for details, refer to Table
            # 1 on page 59 of the picamera2 manual. So we must use MJPEGEncoder instead:
            encoders.MJPEGEncoder(bitrate=self._preview_bitrate),
            outputs.FileOutput(self._preview_output),
            quality=encoders.Quality.HIGH,
            name="lores",
        )

    def capture_file(self, path: str) -> None:
        """Capture an image from the main stream (in full resolution) and save it as a file.

        Blocks until the image is fully saved.

        Args:
            path: The file path where the image should be saved.

        Raises:
            RuntimeError: the method was called before the camera was started, or after it was
              closed.
        """
        if self._camera is None:
            raise RuntimeError(
                "The camera must be configured with the `configure` method before it can be used to"
                + "capture images"
            )

        loguru.logger.debug(f"Capturing and saving image to {path}...")
        request = self._camera.capture_request()
        # The following lines are false-positives in pylint because they're dynamically-generated
        # members:
        request.save("main", path)  # pylint: disable=no-member
        loguru.logger.debug(
            f"Image metadata: {request.get_metadata()}"  # pylint: disable=no-member
        )
        request.release()  # pylint: disable=no-member

    def close(self) -> None:
        """Stop and close the camera.

        The camera can be restarted after being closed by `start()` method again.
        """
        if self._camera is None:
            return

        loguru.logger.debug("Stopping the camera...")
        self._camera.stop_recording()

        loguru.logger.debug("Closing the camera...")
        self._camera.close()
        self._camera = None
        self.controls = None


ExposureModes = typing.Literal["off", "normal", "short", "long"]
WhiteBalanceModes = typing.Literal[
    "off", "auto", "tungsten", "fluorescent", "indoor", "daylight", "cloudy"
]


# FIXME(ethanjli): simplify this class!
class Controls:
    """A wrapper to simplify setting and querying of camera controls & properties."""

    # pylint: disable=too-many-instance-attributes
    # The alternative to having a bunch of instance attributes would be to just do things via dicts,
    # which would lead to a smaller class but would make it impossible to get help from the type
    # checker.

    def __init__(self, camera: picamera2.Picamera2, exposure_range: tuple[int, int]) -> None:
        """Initialize the camera controls.

        Args:
            exposure_range: the minimum and maximum allowed exposure duration values.
        """
        self._camera = camera
        self._exposure_range: typing.Final[tuple[int, int]] = exposure_range

        # Cached values
        self._cache_lock = rwlock.RWLockWrite()
        self._exposure_time: typing.Optional[int] = None
        self._exposure_mode: ExposureModes = "off"
        self._white_balance_mode: WhiteBalanceModes = "off"
        self._image_gain: typing.Optional[float] = None

    # Note: self._camera.controls is thread-safe because it has its own internal threading.Lock()
    # instance. However, if we store any state in Controls, then we should add a reader-writer lock
    # to prevent data races.

    @property
    def sensor_name(self) -> str:
        """Sensor name of the connected camera

        Returns:
            The name of the camera's sensor. One of: `OV5647` (original RPi Camera Module),
            `IMX219` (RPi Camera Module 2), or `IMX477` (RPi High Quality Camera)
        """
        model = self._camera.camera_properties["Model"]
        assert isinstance(model, str)
        return model.upper()

    @property
    def exposure_time(self) -> typing.Optional[int]:
        """Return the last exposure time which was set."""
        with self._cache_lock.gen_rlock():
            return self._exposure_time

    @exposure_time.setter
    def exposure_time(self, exposure_time: int) -> None:
        """Change the camera sensor exposure time.

        Args:
            exposure_time (int): exposure time in µs

        Raises:
            ValueError: if the provided exposure time is outside the allowed range
        """
        if exposure_time < self._exposure_range[0] or exposure_time > self._exposure_range[1]:
            raise ValueError(
                f"Invalid exposure time ({exposure_time}) outside bounds: "
                + f"[{self._exposure_range}]"
            )

        loguru.logger.debug(f"Setting the exposure time to {exposure_time}...")
        with self._cache_lock.gen_wlock():
            self._exposure_time = exposure_time
            self._camera.controls.ExposureTime = self._exposure_time

    @property
    def exposure_mode(self) -> ExposureModes:
        """Return the last exposure mode which was set."""
        return self._exposure_mode

    @exposure_mode.setter
    def exposure_mode(self, mode: ExposureModes) -> None:
        """Change the camera exposure mode.

        Args:
            mode: exposure mode to use.
        """
        loguru.logger.debug(f"Setting the exposure mode to {mode}")

        self._exposure_mode = mode
        if mode == "off":
            self._camera.set_controls({"AeEnable": False})
            return

        modes = {
            "normal": libcamera.controls.AeExposureModeEnum.Normal,
            "short": libcamera.controls.AeExposureModeEnum.Short,
            "long": libcamera.controls.AeExposureModeEnum.Long,
        }
        self._camera.set_controls({"AeEnable": 1, "AeExposureMode": modes[mode]})

    @property
    def white_balance_mode(self) -> WhiteBalanceModes:
        """Return the last white balance mode which was set."""
        return self._white_balance_mode

    @white_balance_mode.setter
    def white_balance_mode(self, mode: WhiteBalanceModes) -> None:
        """Change the white balance mode.

        Args:
            mode: white balance mode to use.
        """
        loguru.logger.debug(f"Setting the white balance mode to {mode}")

        self._white_balance_mode = mode
        if mode == "off":
            self._camera.set_controls({"AwbEnable": False})
            return

        modes = {
            "auto": libcamera.controls.AwbModeEnum.Auto,
            "tungsten": libcamera.controls.AwbModeEnum.Tungsten,
            "fluorescent": libcamera.controls.AwbModeEnum.Fluorescent,
            "indoor": libcamera.controls.AwbModeEnum.Indoor,
            "daylight": libcamera.controls.AwbModeEnum.Daylight,
            "cloudy": libcamera.controls.AwbModeEnum.Cloudy,
        }
        self._camera.set_controls({"AwbEnable": True, "AwbMode": modes[mode]})

    @property
    def white_balance_gains(self) -> tuple[float, float]:
        """Return the last white balance gains which were set."""
        red_gain, blue_gain = self._camera.controls.ColourGains
        assert isinstance(red_gain, float)
        assert isinstance(blue_gain, float)
        return red_gain, blue_gain

    @white_balance_gains.setter
    def white_balance_gains(self, gains: tuple[float, float]) -> None:
        """Change the white balance gains.

        Args:
            gains: red and blue gains, each between 0.0 and 32.0.
        """
        loguru.logger.debug(f"Setting the white balance gain to {gains}")
        red_gain, blue_gain = gains
        min_gain, max_gain = 0.0, 32.0
        if not (min_gain <= red_gain <= max_gain and min_gain <= blue_gain <= max_gain):
            raise ValueError(
                f"Invalid white balance gains (red {red_gain}, blue {blue_gain}) "
                + f"outside bounds: [{min_gain}, {max_gain}]"
            )

        self._camera.controls.ColourGains = gains

    @property
    def image_gain(self) -> typing.Optional[float]:
        """Return the last image gain (analog gain + digital gain) which was set."""
        return self._image_gain

    @image_gain.setter
    def image_gain(self, gain: float) -> None:
        """Change the image gain.

            The camera image gain value should be a floating point number between 1.0 and 16.0
            for the analog gain and the digital gain (used automatically when the sensor’s
            analog gain control cannot go high enough).

        Args:
            gain (float): Image gain to use
        """
        loguru.logger.debug(f"Setting the analogue gain to {gain}")
        min_gain, max_gain = 1.0, 16.0
        if not min_gain <= gain <= max_gain:
            raise ValueError(
                f"Invalid image gain ({gain}) outside bounds: [{min_gain}, {max_gain}]"
            )

        self._image_gain = gain
        self._camera.controls.AnalogueGain = self._image_gain  # DigitalGain

    # FIXME(ethanjli): Delete this if we actually don't need it:
    # @property
    # def image_quality(self):
    #     return self.__image_quality

    # FIXME(ethanjli): Delete this if we actually don't need it:
    # @image_quality.setter
    # def image_quality(self, image_quality):
    #     """Change the output image quality

    #     Args:
    #         image_quality (int): image quality [0,100]
    #     """
    #     loguru.logger.debug(f"Setting image quality to {image_quality}")
    #     if 0 <= image_quality <= 100:
    #         self.__image_quality = image_quality
    #         self._camera.options["quality"] = self.__image_quality
    #     else:
    #         loguru.logger.error(
    #             f"The output image quality specified ({image_quality}) is not valid"
    #         )
    #         raise ValueError


class PreviewStream(io.BufferedIOBase):
    """A thread-safe stream of discrete byte buffers for use in live previews.

    This stream is designed to support at-most-once delivery, so no guarantees are made about
    delivery of every buffer to the consumers: a consumer will skip buffers when it's too busy
    overloaded/blocked. This is a design feature to prevent backpressure on certain consumers
    (e.g. from downstream clients sending the buffer across a network, when the buffer is a large
    image) from degrading stream quality for everyone.

    Note that no thread synchronization is managed for any buffer; consumers must avoid modifying
    the buffer once they have access to it.

    This stream can be used by anything which requires a [io.BufferedIOBase], assuming it never
    splits buffers.
    """

    def __init__(self) -> None:
        """Initialize the stream."""
        self._latest_buffer: typing.Optional[bytes] = None
        # Mutex to prevent data races between readers and writers:
        self._latest_buffer_lock = rwlock.RWLockWrite()
        # Condition variable to allow listeners to wait for a new buffer:
        self._available = threading.Condition()

    def write(self, buffer: typing_extensions.Buffer) -> int:
        """Write the byte buffer as the latest buffer in the stream.

        If readers are accessing the buffer when this method is called, then it may block for a
        while, in order to wait for those readers to finish.

        Returns:
            The length of the byte buffer written.
        """
        b = bytes(buffer)
        with self._latest_buffer_lock.gen_wlock():
            self._latest_buffer = b
        with self._available:
            self._available.notify_all()
        return len(b)

    def wait_next(self) -> None:
        """Wait until the next buffer is available.

        When called, this method blocks until it is awakened by a `update()` call in another
        thread. Once awakened, it returns.
        """
        with self._available:
            self._available.wait()

    def get(self) -> typing.Optional[bytes]:
        """Return the latest buffer in the stream."""
        with self._latest_buffer_lock.gen_rlock():
            return self._latest_buffer
