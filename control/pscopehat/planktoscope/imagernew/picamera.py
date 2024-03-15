################################################################################
# Practical Libraries
################################################################################

# Libraries to manage the camera
from picamera2 import Picamera2
from libcamera import controls
from picamera2.encoders import MJPEGEncoder, Quality
from picamera2.outputs import FileOutput

# Logger library compatible with multiprocessing
from loguru import logger

import time

# import json


################################################################################
# Class for the implementation of Picamera 2
################################################################################
class picamera:
    """This class contains the main definitions of picamera2 monitoring the PlanktoScope's camera"""

    def __init__(self, output, *args, **kwargs):
        """Initialize the picamera class

        Args:
            output (picam_streamer.StreamingOutput): receive encoded video frames directly from the
            encoder and forward them to network sockets
        """
        # Note(ethanjli): if we instantiate Picamera2 here in one process and then call the start
        # method from a child process, then the start method's call of self.__picam.configure
        # will block forever because we've basically duplicated the Picamera2 object when we forked
        # into a child process. So instead we initialize self.__picam to None here, and we'll
        # properly initialize self.__picam in the start method, which is called by a different
        # process than the process which calls __init__.
        self.__picam = None
        self.__controls = {}
        self.__output = output
        self.__sensor_name = ""

    # TODO decide which stream to display (main, lores or raw)
    def start(self, force=False):
        self.__picam = Picamera2()
        logger.debug("Starting up picamera2")
        if force:
            # let's close the camera first
            try:
                self.close()
            except Exception as e:
                logger.exception(f"Closing picamera2 failed because of {e}")

        # Configure the camera with a video configuration for streaming video frames
        half_resolution = [dim // 2 for dim in self.__picam.sensor_resolution]
        main_stream = {"size": half_resolution}
        lores_stream = {"size": (640, 480)}
        # Note(ethanjli): if we use "lores" as our encode argument, we must use the MJPEGEncoder
        # instead of JpegEncoder. This is because on the RPi4 the lores stream only outputs as
        # YUV420, but JpegEncoder only accepts RGB. By contrast, MJPEGEncoder can handle YUV420.
        # If we do need RGB output for something, we'll have to use the "main" stream instead of the
        # "lores" stream for that. For details, refer to Table 1 on page 59 of the picamera2 manual
        # at https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf.
        config = self.__picam.create_video_configuration(
            main_stream, lores_stream, encode="lores"
        )
        self.__picam.configure(config)

        # Extract camera properties from picamera2 instance
        self.__sensor_name = self.__picam.camera_properties["Model"].upper()
        self.__width = self.__picam.sensor_resolution[0]
        self.__height = self.__picam.sensor_resolution[1]

        # Start recording with video encoding and writing video frames
        # Note(ethanjli): see note above about JpegEncoder vs. MJPEGEncoder compatibility with
        # "lores" streams!
        self.__picam.start_recording(
            MJPEGEncoder(), FileOutput(self.__output), Quality.HIGH
        )

    # NOTE function drafted as a target of the camera thread (simple version)
    """def preview_picam(self):
        try:
            self.__picam.start()
        except Exception as e:
            logger.exception(
                f"An exception has occured when starting up picamera2: {e}"
            )
            try:
                self.__picam.start(True)
            except Exception as e:
                logger.exception(
                    f"A second exception has occured when starting up picamera2: {e}"
                )
                logger.error("This error can't be recovered from, terminating now")
                raise e
        try:
            while not self.stop_event.is_set():
                if not self.command_queue.empty():
                    try:
                        command = self.command_queue.get(timeout=0.1)
                    except Exception as e:
                        logger.exception(f"An error has occurred while handling a command: {e}")
                pass
                time.sleep(0.01)
        finally:
            self.__picam.stop()
            self.__picam.close()"""

    @property
    def sensor_name(self):
        """Sensor name of the connected camera

        Returns:
            string: Sensor name. One of OV5647 (cam v1), IMX219 (cam v2.1), IMX477(ca HQ)
        """
        return self.__sensor_name

    @property
    def width(self):
        return self.__width

    @property
    def height(self):
        return self.__height

    @property
    def exposure_time(self):
        return self.__exposure_time

    @exposure_time.setter
    def exposure_time(self, exposure_time):
        """Change the camera sensor exposure time (shutter speed) in microseconds
        between 0 and 66666 (according to "camera_controls" property).

        Args:
            exposure_time (int): exposure time in µs
        """
        logger.debug(f"Setting the exposure time to {exposure_time}")
        if 0 < exposure_time < 66666:
            self.__exposure_time = exposure_time
            with self.__picam.controls as ctrls:
                ctrls.ExposureTime = self.__exposure_time
        else:
            logger.error(f"The exposure time specified ({exposure_time}) is not valid")
            raise ValueError

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
        logger.debug(f"Setting the exposure mode to {mode}")
        modes = {
            "off": False,
            "normal": controls.AeExposureModeEnum.Normal,
            "short": controls.AeExposureModeEnum.Short,
            "long": controls.AeExposureModeEnum.Long,
        }
        if mode in modes:
            self.__exposure_mode = modes[mode]
            if mode == "off":
                self.__picam.set_controls({"AeEnable": self.__exposure_mode})
            else:
                self.__picam.set_controls(
                    {"AeEnable": 1, "AeExposureMode": self.__exposure_mode}
                )  # "AeEnable": 1,
        else:
            logger.error(f"The exposure mode specified ({mode}) is not valid")
            raise ValueError

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
        logger.debug(f"Setting the white balance mode to {mode}")
        modes = {
            "off": False,
            "auto": controls.AwbModeEnum.Auto,
            "tungsten": controls.AwbModeEnum.Tungsten,
            "fluorescent": controls.AwbModeEnum.Fluorescent,
            "indoor": controls.AwbModeEnum.Indoor,
            "daylight": controls.AwbModeEnum.Daylight,
            "cloudy": controls.AwbModeEnum.Cloudy,
        }
        if mode in modes:
            self.__white_balance = modes[mode]
            if mode == "off":
                self.__picam.set_controls({"AwbEnable": self.__white_balance})
            else:
                self.__picam.set_controls(
                    {"AwbEnable": 1, "AwbMode": self.__white_balance}
                )  # "AwbEnable": 1,
        else:
            logger.error(
                f"The camera white balance mode specified ({mode}) is not valid"
            )
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
        logger.debug(f"Setting the white balance gain to {gain}")
        if (0.0 <= gain[0] <= 32.0) and (0.0 <= gain[1] <= 32.0):
            self.__white_balance_gain = gain
            with self.__picam.controls as ctrls:
                ctrls.ColourGains = self.__white_balance_gain
        else:
            logger.error(
                f"The camera white balance gain specified ({gain}) is not valid"
            )
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
        logger.debug(f"Setting the analogue gain to {gain}")
        if 1.0 <= gain <= 16.0:
            self.__image_gain = gain
            with self.__picam.controls as ctrls:
                ctrls.AnalogueGain = self.__image_gain  # DigitalGain
        else:
            logger.error(f"The camera image gain specified ({gain}) is not valid")
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
        logger.debug(f"Setting image quality to {image_quality}")
        if 0 <= image_quality <= 100:
            self.__image_quality = image_quality
            self.__picam.options["quality"] = self.__image_quality
        else:
            logger.error(
                f"The output image quality specified ({image_quality}) is not valid"
            )
            raise ValueError

    # TODO complete (if needed) the setters and getters of resolution & iso

    # TODO capture images in full/high resolution "while the server is serving indefinitely"
    def capture(self, path=""):
        """Capture an image (in full resolution)

        Args:
            path (str, optional): Path to image file. Defaults to "".
        """
        logger.debug(f"Capturing an image to {path}")
        # metadata = self.__picam.capture_file(path) #use_video_port
        request = self.__picam.capture_request()
        request.save("main", path)

        time.sleep(0.1)
        request.release()

    def stop(self):
        """Release the camera"""
        logger.debug("Releasing the camera now")
        self.__picam.stop_preview()
        self.__picam.stop_recording()

    def close(self):
        """Close the camera"""
        logger.debug("Closing the camera now")
        self.__picam.close()
