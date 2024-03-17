"""camera provides type-annotated hardware abstractions for Raspberry Pi cameras."""

import io
import typing

# import libcamera  # type: ignore
import loguru
import picamera2  # type: ignore
from picamera2 import encoders, outputs


class Controls:
    """A wrapper to simplify setting and querying of camera controls & properties."""

    def __init__(self, camera: picamera2.Picamera2) -> None:
        self._camera = camera

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
        return self._camera.camera_properties["Model"].upper()

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @property
    # def main_dimensions(self) -> tuple[int, int]:
    #     return self._camera.sensor_resolution

    # TODO(ethanjli): Delete this if we actually don't need it:
    # FIXME(ethanjli): does this actually return an int?
    # @property
    # def exposure_time(self) -> int:
    #     _, _, default = self._camera.controls.ExposureTime
    #     return default

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @exposure_time.setter
    # def exposure_time(self, exposure_time: int) -> None:
    #     """Change the camera sensor exposure time.

    #     Args:
    #         exposure_time (int): exposure time in µs

    #     Raises:
    #         ValueError: if the provided exposure time is outside the allowed range
    #     """
    #     min_value, max_value, _ = self._camera.controls.ExposureTime
    #     if exposure_time < min_value or exposure_time > max_value:
    #         raise ValueError(
    #             f"Invalid exposure time ({exposure_time}) outside bounds: "
    #             + f"[{min_value}, {max_value}]"
    #         )

    #     loguru.logger.debug(f"Setting the exposure time to {exposure_time}...")
    #     self._exposure_time = exposure_time
    #     self._camera.controls.ExposureTime = self._exposure_time

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @property
    # def exposure_mode(self):
    #     return self.__exposure_mode

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @exposure_mode.setter
    # def exposure_mode(self, mode):
    #     """Change the camera exposure mode

    #     Is one of off, normal, short, long

    #     Args:
    #         mode (string): exposure mode to use
    #     """
    #     loguru.logger.debug(f"Setting the exposure mode to {mode}")
    #     EXPOSURE_MODES = {
    #         "off": False,
    #         "normal": libcamera.controls.AeExposureModeEnum.Normal,
    #         "short": libcamera.controls.AeExposureModeEnum.Short,
    #         "long": libcamera.controls.AeExposureModeEnum.Long,
    #     }
    #     if mode not in EXPOSURE_MODES:
    #         loguru.logger.error(f"The exposure mode specified ({mode}) is not valid")
    #         raise ValueError

    #     self.__exposure_mode = EXPOSURE_MODES[mode]
    #     if mode == "off":
    #         self._camera.set_controls({"AeEnable": self.__exposure_mode})
    #         return

    #     self._camera.set_controls({"AeEnable": 1, "AeExposureMode": self.__exposure_mode})

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @property
    # def white_balance(self):
    #     return self.__white_balance

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @white_balance.setter
    # def white_balance(self, mode):
    #     """Change the camera white balance mode

    #     Is one of off, auto, tungsten, fluorescent,
    #     indoor, daylight, cloudy

    #     Args:
    #         mode (string): white balance mode to use
    #     """
    #     loguru.logger.debug(f"Setting the white balance mode to {mode}")
    #     modes = {
    #         "off": False,
    #         "auto": libcamera.controls.AwbModeEnum.Auto,
    #         "tungsten": libcamera.controls.AwbModeEnum.Tungsten,
    #         "fluorescent": libcamera.controls.AwbModeEnum.Fluorescent,
    #         "indoor": libcamera.controls.AwbModeEnum.Indoor,
    #         "daylight": libcamera.controls.AwbModeEnum.Daylight,
    #         "cloudy": libcamera.controls.AwbModeEnum.Cloudy,
    #     }
    #     if mode in modes:
    #         self.__white_balance = modes[mode]
    #         if mode == "off":
    #             self._camera.set_controls({"AwbEnable": self.__white_balance})
    #         else:
    #             self._camera.set_controls(
    #                 {"AwbEnable": 1, "AwbMode": self.__white_balance}
    #             )  # "AwbEnable": 1,
    #     else:
    #         loguru.logger.error(f"The camera white balance mode specified ({mode}) is not valid")
    #         raise ValueError

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @property
    # def white_balance_gain(self):
    #     return self.__white_balance_gain

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @white_balance_gain.setter
    # def white_balance_gain(self, gain):
    #     """Change the camera white balance gain

    #         The gain value should be a floating point number between 0.0 and 32.0 for
    #         the red and the blue gain.

    #     Args:
    #         gain (tuple of float): Red gain and blue gain to use
    #     """
    #     loguru.logger.debug(f"Setting the white balance gain to {gain}")
    #     if (0.0 <= gain[0] <= 32.0) and (0.0 <= gain[1] <= 32.0):
    #         self.__white_balance_gain = gain
    #         self._camera.controls.ColourGains = self.__white_balance_gain
    #     else:
    #         loguru.logger.error(f"The camera white balance gain specified ({gain}) is not valid")
    #         raise ValueError

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @property
    # def image_gain(self):
    #     return self.__image_gain

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @image_gain.setter
    # def image_gain(self, gain):
    #     """Change the camera image gain

    #         The camera image gain value should be a floating point number between 1.0 and 16.0
    #         for the analog gain and the digital gain (used automatically when the sensor’s
    #         analog gain control cannot go high enough).

    #     Args:
    #         gain (float): Image gain to use
    #     """
    #     loguru.logger.debug(f"Setting the analogue gain to {gain}")
    #     if 1.0 <= gain <= 16.0:
    #         self.__image_gain = gain
    #         self._camera.controls.AnalogueGain = self.__image_gain  # DigitalGain
    #     else:
    #         loguru.logger.error(f"The camera image gain specified ({gain}) is not valid")
    #         raise ValueError

    # TODO(ethanjli): Delete this if we actually don't need it:
    # @property
    # def image_quality(self):
    #     return self.__image_quality

    # TODO(ethanjli): Delete this if we actually don't need it:
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

    # TODO complete (if needed) the setters and getters of resolution & iso


class PiCamera:
    """A thread-safe wrapper around a picamera2-based camera.

    Attributes:
        controls: a [Controls] instance associated with the camera. Uninitialized until the
          `start()` method is called.
    """

    def __init__(
        self,
        preview_output: io.BufferedIOBase,
        preview_size=(640, 480),
        preview_bitrate: typing.Optional[int] = None,
    ) -> None:
        """Set up state needed to initialize the camera, but don't actually start the camera.

        Args:
            preview_output: an image stream which this `PiCamera` instance will write camera preview
              images to.
            preview_size: a 2-tuple of the width and height (in pixels) of the camera preview.
            preview_bitrate: the bitrate of the preview stream; defaults to a bitrate for a medium
              quality stream.
        """
        self.controls: typing.Optional[Controls] = None

        self._preview_output = preview_output
        # Note(ethanjli): `__init__()` may be called from a different process than the one which
        # will call the `start()` method, so we must initialize `self._camera` to None here, and
        # we'll properly initialize it in the `start()` method:
        self._camera: typing.Optional[picamera2.Picamera2] = None

        # TODO(ethanjli): Delete these if we actually don't need them:
        # self._main_format: typing.Optional[str] = None
        # self._main_size: typing.Optional[tuple[int, int]] = None
        self._preview_size: tuple[int, int] = preview_size
        self._preview_bitrate: typing.Optional[int] = preview_bitrate

    def start(self) -> None:
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
        # TODO(ethanjli): Delete these if we actually don't need them:
        # self._main_format = config["main"]["format"]
        # self._main_size = config["main"]["size"]
        # self._preview_size = config["preview"]["size"]
        self._camera.configure(config)

        self.controls = Controls(self._camera)

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

    def close(self) -> None:
        """Stop and close the camera.

        The camera can be restarted after being closed by calling the `configure()` and `start()`
        methods again.
        """
        if self._camera is None:
            return

        loguru.logger.debug("Stopping the camera...")
        self._camera.stop_recording()

        loguru.logger.debug("Closing the camera...")
        self._camera.close()
        self._camera = None
        self.controls = None

    def capture_file(self, path: str) -> None:
        """Capture an image from the main stream (in full resolution) and save it as a file.

        Blocks until the image is fully saved.

        Args:
            path: The file path where the image should be saved.

        Raises:
            RuntimeError: the method was called before the camera was configured
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
