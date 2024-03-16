"""camera provides type-annotated hardware abstractions for Raspberry Pi cameras."""

import io
import time
import typing

import libcamera  # type: ignore
import loguru
import picamera2  # type: ignore
from picamera2 import encoders, outputs


class PiCamera:
    """A thread-safe wrapper around a picamera2-based camera.

    Attributes:
        preview_dimensions: a 2-tuple consisting of the width and height (in units of pixels) of the
          preview stream.
    """

    def __init__(self, preview_output: io.BufferedIOBase) -> None:
        """Set up state needed to initialize the camera, but don't actually start the camera.

        Args:
            preview_output: an image stream which this `PiCamera` instance will write camera preview
              images to.
        """
        self._preview_output = preview_output

        # Note(ethanjli): `__init__()` may be called from a different process than the one which
        # will call the `start()` method, so we must initialize `self._camera` to None here, and
        # we'll properly initialize it in the `start()` method:
        self._camera: typing.Optional[picamera2.Picamera2] = None
        self._configurer: typing.Optional[Configurer] = None

        self.preview_dimensions = (640, 480)

    def start(self) -> None:
        """Start the camera, including output to the preview stream."""
        loguru.logger.debug("Starting the camera...")
        self._camera = picamera2.Picamera2()
        self._configurer = Configurer(self._camera)

        # Configure the camera with a video configuration for streaming video frames
        main_stream = {"size": self._camera.sensor_resolution}
        lores_stream = {"size": self.preview_dimensions}
        # Note(ethanjli): if we use "lores" as our encode argument, we must use the MJPEGEncoder
        # instead of JpegEncoder. This is because on the RPi4 the lores stream only outputs as
        # YUV420, but JpegEncoder only accepts RGB. By contrast, MJPEGEncoder can handle YUV420.
        # If we do need RGB output for something, we'll have to use the "main" stream instead of the
        # "lores" stream for that. For details, refer to Table 1 on page 59 of the picamera2 manual
        # at https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf.
        # TODO decide which stream to display (main, lores or raw)
        config = self._camera.create_video_configuration(main_stream, lores_stream, encode="lores")
        self._camera.configure(config)

        # Note(ethanjli): see note above about JpegEncoder vs. MJPEGEncoder compatibility with
        # "lores" streams!
        self._camera.start_recording(
            # FIXME(ethanjli): let's not use file output here!
            encoders.MJPEGEncoder(),
            outputs.FileOutput(self._preview_output),
            encoders.Quality.HIGH,
        )

    # NOTE function drafted as a target of the camera thread (simple version)
    # def preview_picam(self):
    #     try:
    #         self._camera.start()
    #     except Exception as e:
    #         logger.exception(
    #             f"An exception has occured when starting up picamera2: {e}"
    #         )
    #         try:
    #             self._camera.start(True)
    #         except Exception as e:
    #             logger.exception(
    #                 f"A second exception has occured when starting up picamera2: {e}"
    #             )
    #             logger.error("This error can't be recovered from, terminating now")
    #             raise e
    #     try:
    #         while not self.stop_event.is_set():
    #             if not self.command_queue.empty():
    #                 try:
    #                     command = self.command_queue.get(timeout=0.1)
    #                 except Exception as e:
    #                     logger.exception(f"An error has occurred while handling a command: {e}")
    #             pass
    #             time.sleep(0.01)
    #     finally:
    #         self._camera.stop()
    #         self._camera.close()

    # TODO capture images in full/high resolution "while the server is serving indefinitely"
    def capture(self, path=""):
        """Capture an image (in full resolution)

        Args:
            path (str, optional): Path to image file. Defaults to "".
        """
        loguru.logger.debug(f"Capturing an image to {path}")
        # metadata = self._camera.capture_file(path) #use_video_port
        request = self._camera.capture_request()
        request.save("main", path)

        time.sleep(0.1)
        request.release()

    def stop(self):
        """Release the camera"""
        loguru.logger.debug("Releasing the camera now")
        self._camera.stop_preview()
        self._camera.stop_recording()

    def close(self):
        """Close the camera"""
        loguru.logger.debug("Closing the camera now")
        self._camera.close()


class Configurer:
    def __init__(self, camera: picamera2.Picamera2) -> None:
        """A wrapper to simplify setting and querying of camera settings."""
        self._camera = camera

    @property
    def sensor_name(self) -> str:
        """Sensor name of the connected camera

        Returns:
            The name of the camera's sensor. One of: `OV5647` (original RPi Camera Module),
            `IMX219` (RPi Camera Module 2), or `IMX477` (RPi High Quality Camera)
        """
        return self._camera.camera_properties["Model"].upper()

    @property
    def main_dimensions(self) -> tuple[int, int]:
        return self._camera.sensor_resolution

    # FIXME(ethanjli): does this actually return an int?
    @property
    def exposure_time(self) -> int:
        with self._camera.controls as controls:
            _, _, default = controls.ExposureTime
            return default

    @exposure_time.setter
    def exposure_time(self, exposure_time: int) -> None:
        """Change the camera sensor exposure time.

        Args:
            exposure_time (int): exposure time in µs

        Raises:
            ValueError: if the provided exposure time is outside the allowed range
        """
        with self._camera.controls as controls:
            min_value, max_value, _ = controls.ExposureTime
            if exposure_time < min_value or exposure_time > max_value:
                raise ValueError(
                    f"Invalid exposure time ({exposure_time}) outside bounds: "
                    + f"[{min_value}, {max_value}]"
                )

            loguru.logger.debug(f"Setting the exposure time to {exposure_time}...")
            self._exposure_time = exposure_time
            controls.ExposureTime = self._exposure_time

    @property
    def exposure_mode(self):
        return self.__exposure_mode

    @exposure_mode.setter
    def exposure_mode(self, mode):
        """Change the camera exposure mode

        Is one of off, normal, short, long

        Args:
            mode (string): exposure mode to use
        """
        loguru.logger.debug(f"Setting the exposure mode to {mode}")
        EXPOSURE_MODES = {
            "off": False,
            "normal": libcamera.controls.AeExposureModeEnum.Normal,
            "short": libcamera.controls.AeExposureModeEnum.Short,
            "long": libcamera.controls.AeExposureModeEnum.Long,
        }
        if mode not in EXPOSURE_MODES:
            loguru.logger.error(f"The exposure mode specified ({mode}) is not valid")
            raise ValueError

        self.__exposure_mode = EXPOSURE_MODES[mode]
        if mode == "off":
            self._camera.set_controls({"AeEnable": self.__exposure_mode})
            return

        self._camera.set_controls({"AeEnable": 1, "AeExposureMode": self.__exposure_mode})

    @property
    def white_balance(self):
        return self.__white_balance

    @white_balance.setter
    def white_balance(self, mode):
        """Change the camera white balance mode

        Is one of off, auto, tungsten, fluorescent,
        indoor, daylight, cloudy

        Args:
            mode (string): white balance mode to use
        """
        loguru.logger.debug(f"Setting the white balance mode to {mode}")
        modes = {
            "off": False,
            "auto": libcamera.controls.AwbModeEnum.Auto,
            "tungsten": libcamera.controls.AwbModeEnum.Tungsten,
            "fluorescent": libcamera.controls.AwbModeEnum.Fluorescent,
            "indoor": libcamera.controls.AwbModeEnum.Indoor,
            "daylight": libcamera.controls.AwbModeEnum.Daylight,
            "cloudy": libcamera.controls.AwbModeEnum.Cloudy,
        }
        if mode in modes:
            self.__white_balance = modes[mode]
            if mode == "off":
                self._camera.set_controls({"AwbEnable": self.__white_balance})
            else:
                self._camera.set_controls(
                    {"AwbEnable": 1, "AwbMode": self.__white_balance}
                )  # "AwbEnable": 1,
        else:
            loguru.logger.error(f"The camera white balance mode specified ({mode}) is not valid")
            raise ValueError

    @property
    def white_balance_gain(self):
        return self.__white_balance_gain

    @white_balance_gain.setter
    def white_balance_gain(self, gain):
        """Change the camera white balance gain

            The gain value should be a floating point number between 0.0 and 32.0 for
            the red and the blue gain.

        Args:
            gain (tuple of float): Red gain and blue gain to use
        """
        loguru.logger.debug(f"Setting the white balance gain to {gain}")
        if (0.0 <= gain[0] <= 32.0) and (0.0 <= gain[1] <= 32.0):
            self.__white_balance_gain = gain
            with self._camera.controls as controls:
                controls.ColourGains = self.__white_balance_gain
        else:
            loguru.logger.error(f"The camera white balance gain specified ({gain}) is not valid")
            raise ValueError

    @property
    def image_gain(self):
        return self.__image_gain

    @image_gain.setter
    def image_gain(self, gain):
        """Change the camera image gain

            The camera image gain value should be a floating point number between 1.0 and 16.0
            for the analog gain and the digital gain (used automatically when the sensor’s
            analog gain control cannot go high enough).

        Args:
            gain (float): Image gain to use
        """
        loguru.logger.debug(f"Setting the analogue gain to {gain}")
        if 1.0 <= gain <= 16.0:
            self.__image_gain = gain
            with self._camera.controls as controls:
                controls.AnalogueGain = self.__image_gain  # DigitalGain
        else:
            loguru.logger.error(f"The camera image gain specified ({gain}) is not valid")
            raise ValueError

    @property
    def image_quality(self):
        return self.__image_quality

    @image_quality.setter
    def image_quality(self, image_quality):
        """Change the output image quality

        Args:
            image_quality (int): image quality [0,100]
        """
        loguru.logger.debug(f"Setting image quality to {image_quality}")
        if 0 <= image_quality <= 100:
            self.__image_quality = image_quality
            self._camera.options["quality"] = self.__image_quality
        else:
            loguru.logger.error(
                f"The output image quality specified ({image_quality}) is not valid"
            )
            raise ValueError

    # TODO complete (if needed) the setters and getters of resolution & iso
