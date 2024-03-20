"""hardware provides basic I/O abstractions for camera hardware."""

import io
import threading
import typing

import loguru
import picamera2  # type: ignore
import typing_extensions
from picamera2 import encoders, outputs
from readerwriterlock import rwlock


class WhiteBalanceGains(typing.NamedTuple):
    """Manual white balance gains."""

    red: float
    blue: float


class SettingsValues(typing.NamedTuple):
    """Values for camera settings.

    Fields with `None` values should be ignored as if they were not set.
    """

    auto_exposure: typing.Optional[bool] = None
    exposure_time: typing.Optional[int] = None  # μs; must be within frame_duration_limits
    frame_duration_limits: typing.Optional[tuple[int, int]] = None  # μs
    image_gain: typing.Optional[float] = None  # must be within [0.0, 16.0]
    brightness: typing.Optional[float] = None  # must be within [-1.0, 1.0]
    contrast: typing.Optional[float] = None  # must be within [0.0, 32.0]
    auto_white_balance: typing.Optional[bool] = None
    white_balance_gains: typing.Optional[WhiteBalanceGains] = None  # must be within [0.0, 32.0]
    sharpness: typing.Optional[float] = None  # must be within [0.0, 16.0]
    jpeg_quality: typing.Optional[int] = None  # must be within [0, 95]
    # Note(ethanjli): we can also expose other settings/properties in a similar way, but we don't
    # need them yet and we're trying to minimize the amount of code we have to maintain, so for now
    # I haven't implemented them.

    def validate(self) -> list[str]:
        """Look for values which are invalid because they're out-of-range.

        Returns:
            A list of strings, each representing a validation error.
        """
        value: typing.Any = None
        errors = self._validate_exposure_time()
        if (value := self.image_gain) is not None and not 0.0 <= value <= 16.0:
            errors.append(f"Image gain out of range [0.0, 16.0]: {value}")
        if (value := self.brightness) is not None and not -1.0 <= value <= 1.0:
            errors.append(f"Brightness out of range [-1.0, 1.0]: {value}")
        if (value := self.contrast) is not None and not 0.0 <= value <= 32.0:
            errors.append(f"Contrast out of range [0.0, 32.0]: {value}")
        if (value := self.white_balance_gains) is not None and not 0.0 <= value.red <= 32.0:
            errors.append(f"Red white-balance gain out of range [0.0, 32.0]: {value.red}")
        if (value := self.white_balance_gains) is not None and not 0.0 <= value.blue <= 32.0:
            errors.append(f"Blue white-balance gain out of range [0.0, 32.0]: {value.blue}")
        if (value := self.sharpness) is not None and not 0.0 <= value <= 16.0:
            errors.append(f"Sharpness out of range [0.0, 16.0]: {value}")
        if (value := self.jpeg_quality) is not None and not 0 <= value <= 95:
            errors.append(f"JPEG quality out of range [0, 95]: {value}")

        return errors

    def _validate_exposure_time(self) -> list[str]:
        """Check whether exposure_time is consistent with frame_duration_limits."""
        if (value := self.exposure_time) is None:
            return []

        if (limits := self.frame_duration_limits) is None:
            if value < 0:
                return [f"Exposure time out of range [0, +Inf]: {value}"]
            return []

        # This is a pylint false-positive, since mypy knows `limits` is an unpackable tuple:
        min_limit, max_limit = limits  # pylint: disable=unpacking-non-sequence
        if not min_limit <= value <= max_limit:
            return [f"Exposure time out of range [{min_limit}, {max_limit}]: {value}"]

        return []

    def has_values(self) -> bool:
        """Check whether any values are non-`None`."""
        # pylint complains that this namedtuple has no `_asdict()` method even though mypy is fine;
        # this is a false positive:
        # pylint: disable-next=no-member
        return any(value is not None for value in self._asdict().values())

    def overlay(self, updates: "SettingsValues") -> "SettingsValues":
        """Create a new instance where provided non-`None` values overwrite existing values."""
        # pylint complains that this namedtuple has no `_asdict()` method even though mypy is fine;
        # this is a false positive:
        # pylint: disable-next=no-member
        return self._replace(
            **{key: value for key, value in updates._asdict().items() if value is not None}
        )

    def as_picamera2_controls(self) -> dict[str, typing.Any]:
        """Create an equivalent dict of values for picamera2's camera controls."""
        result = {
            "AeEnable": self.auto_exposure,
            "ExposureTime": self.exposure_time,
            "AnalogueGain": self.image_gain,
            "Brightness": self.brightness,
            "Contrast": self.contrast,
            "AwbEnable": self.auto_white_balance,
            "ColourGains": self.white_balance_gains,
            "Sharpness": self.sharpness,
        }
        return {key: value for key, value in result.items() if value is not None}

    def as_picamera2_options(self) -> dict[str, typing.Any]:
        """Create an equivalent dict of values suitable for picamera2's camera options."""
        result = {"quality": self.jpeg_quality}
        return {key: value for key, value in result.items() if value is not None}


def _picamera2_config_to_settings_values(config: dict[str, typing.Any]) -> SettingsValues:
    """Create a SettingsValues from a picamera2 pre-start configuration.

    Raises:
        ValueError: the configuration does not contain the required fields or values.
    """
    frame_duration_limits = config["controls"]["FrameDurationLimits"]
    return SettingsValues(frame_duration_limits=frame_duration_limits)


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
        # Initialization settings (must be set before camera starts):
        self._preview_size: tuple[int, int] = preview_size
        self._preview_bitrate: typing.Optional[int] = preview_bitrate

        # Cached settings (can be adjusted while camera runs):
        self._cached_settings_lock = rwlock.RWLockWrite()
        self._cached_settings = SettingsValues()

        # Read-only properties:
        self._config: dict[str, typing.Any] = {}

        # I/O:
        self._preview_output = preview_output
        self._camera: typing.Optional[picamera2.Picamera2] = None

    def open(self) -> None:
        """Start the camera in the background, including output to the preview stream.

        Blocks until the camera has started.
        """
        loguru.logger.debug("Configuring the camera...")
        self._camera = picamera2.Picamera2()

        # We use the `create_still_configuration` to get the best defaults for still images from the
        # "main" stream:
        self._config = self._camera.create_still_configuration(
            main={"size": self._camera.sensor_resolution},
            lores={"size": self._preview_size},
            # We need at least three buffers to allow the preview to continue receiving frames
            # smoothly from the lores stream while a buffer is reserved for saving an image from
            # the main stream:
            buffer_count=3,
        )
        loguru.logger.debug(f"Camera configuration: {self._config}")
        self._camera.configure(self._config)
        self._cached_settings = _picamera2_config_to_settings_values(self._config)

        # Note(ethanjli): we could apply initial camera controls settings here before we start
        # recording, but we don't really have a need to do that, and it's simpler to require
        # the client code to wait until after the camera has started recording before allowing
        # settings changes.

        loguru.logger.debug("Starting the camera...")
        self._camera.start_recording(
            # For compatibility with the RPi 4 (which must use YUV420 for lores stream output), we
            # cannot use JpegEncoder (which only accepts RGB, not YUV); for details, refer to Table
            # 1 on page 59 of the picamera2 manual. So we must use MJPEGEncoder instead:
            encoders.MJPEGEncoder(bitrate=self._preview_bitrate),
            outputs.FileOutput(self._preview_output),
            quality=encoders.Quality.HIGH,
            name="lores",
        )

    @property
    def settings(self) -> SettingsValues:
        """Returns an immutable copy of the camera settings values."""
        with self._cached_settings_lock.gen_rlock():
            return self._cached_settings

    @settings.setter
    def settings(self, updates: SettingsValues) -> None:
        """Updates adjustable camera settings from all provided non-`None` values.

        Fields provided with `None` values are ignored.

        Raises:
            RuntimeError: the method was called before the camera was started, or after it was
              closed.
            ValueError: some of the provided values are out of the allowed ranges.
        """
        if not updates.has_values():
            return
        if self._camera is None:
            raise RuntimeError("The camera has not been started yet!")

        loguru.logger.debug(f"Applying camera settings updates: {updates}")
        with self._cached_settings_lock.gen_wlock():
            new_values = self._cached_settings.overlay(updates)
            loguru.logger.debug(f"New camera settings will be: {new_values}")
            if errors := new_values.validate():
                raise ValueError(f"Invalid settings: {'; '.join(errors)}")
            #loguru.logger.debug(f"Controls for picamera2: {updates.as_picamera2_controls()}")
            loguru.logger.debug(f"Controls for picamera2: {new_values.as_picamera2_controls()}")
            # FIXME(ethanjli): for some reason, exposure time doesn't actually change; and the other
            # settings cause a crash. Maybe one of the values is in an incorrect format? Let's add
            # a test in test_preview.py for easier debugging/testing!
            #self._camera.set_controls(updates.as_picamera2_controls())
            self._camera.set_controls(new_values.as_picamera2_controls())
            for key, value in updates.as_picamera2_options().items():
                self._camera.options[key] = value
            self._cached_settings = new_values

    @property
    def sensor_name(self) -> str:
        """Name of the camera sensor.

        Returns:
            Usually one of: `IMX219` (RPi Camera Module 2, used in PlanktoScope hardware v2.1)
            or `IMX477` (RPi High Quality Camera, used in PlanktoScope hardware v2.3+).

        Raises:
            RuntimeError: the method was called before the camera was started, or after it was
              closed.
        """
        if self._camera is None:
            raise RuntimeError("The camera has not been started yet!")

        model = self._camera.camera_properties["Model"]
        assert isinstance(model, str)
        return model.upper()

    @property
    def capture_size(self) -> tuple[int, int]:
        """The width and height of images captured from the main stream."""
        # Note(ethanjli): we can also expose the preview size in a similar way, just using "lores"
        # instead of "main". But we don't need that yet and we're trying to minimize the amount of
        # code we have to maintain, so for now I haven't implemented it.
        assert "size" in self._config["main"]
        size = self._config["main"]["size"]
        assert isinstance(size, tuple)
        assert len(size) == 2
        assert isinstance(size[0], int)
        assert isinstance(size[1], int)
        return size

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
            raise RuntimeError("The camera has not been started yet!")

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
        # Note(ethanjli): when picamera2 itself crashes while recording in the background, calling
        # `stop_recording()` causes a deadlock! I don't know how to work around that deadlock; this
        # might be an upstream bug which we could fix by upgrading to RPi OS 12, or maybe we need to
        # file an issue with upstream (i.e. in the picamera2 GitHub repo...for now, we'll just try
        # to avoid causing crashes in picamera2 and worry about this problem another day 🤡
        self._camera.stop_recording()

        loguru.logger.debug("Closing the camera...")
        self._camera.close()
        self._camera = None
        self._cached_settings = SettingsValues()


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
