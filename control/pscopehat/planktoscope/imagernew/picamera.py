################################################################################
# Practical Libraries
################################################################################

# Libraries to manage the camera 
from picamera2 import Picamera2, Preview
from libcamera import controls
from picamera2.encoders import JpegEncoder, MJPEGEncoder, Quality
from picamera2.outputs import FileOutput

# Logger library compatible with multiprocessing
from loguru import logger

# Library for path and filesystem manipulations
import os

# Libraries to get date and time for folder name and filename
import datetime
import time

#import json

################################################################################
# Class for the implementation of Picamera 2
################################################################################
class picamera:
    def __init__(self, output, *args, **kwargs):
        self.__picam = Picamera2()
        self.__controls = {}
        self.__output = output
        self.__sensor_name = ""

    def start(self, force=False):
        logger.debug("Starting up picamera2")
        if force:
            # let's close the camera first
            try:
                self.close()
            except Exception as e:
                logger.exception(f"Closing picamera2 failed because of {e}")

        # Configure the camera with a video configuration for streaming video frames
        config = self.__picam.create_video_configuration(main={"size": (800, 600)})
        self.__picam.configure(config)

        # Extract camera properties from picamera2 instance
        self.__sensor_name = self.__picam.camera_properties["Model"]
        self.__width = self.__picam.camera_properties["PixelArraySize"][0]
        self.__height = self.__picam.camera_properties["PixelArraySize"][1]

        # Start recording with video encoding and writing video frames
        self.__picam.start_recording(JpegEncoder(), FileOutput(self.__output), Quality.HIGH)

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
    def resolution(self):
        return self.__resolution

    """@resolution.setter
    def resolution(self, resolution):
        Change the camera image resolution

        For a full FOV, allowable resolutions are:
        - (3280,2464), (1640,1232), (1640,922) for Camera V2.1
        - (2028,1520), (4056,3040) for HQ Camera


        Args:
            resolution (tuple of int): resolution to set the camera to
        
        logger.debug(f"Setting the resolution to {resolution}")
        if resolution in [
            (3280, 2464),
            (1640, 1232),
            (1640, 922),
            (2028, 1520),
            (4056, 3040),
        ]:
            self.__resolution = resolution
            self.__picam.resolution = self.__resolution
        else:
            logger.error(f"The resolution specified ({resolution}) is not valid")
            raise ValueError

    @property
    def iso(self):
        return self.__iso

    @iso.setter
    def iso(self, iso):
        Change the camera iso number

        Iso number will be rounded to the closest one of
        0, 100, 200, 320, 400, 500, 640, 800.
        If 0, Iso number will be chosen automatically by the camera

        Args:
            iso (int): Iso number
        
        logger.debug(f"Setting the iso number to {iso}")

        if 0 <= iso <= 800:
            self.__iso = iso
            self.__picam.iso = self.__iso
            #self.__wait_for_output("Change: iso")
        else:
            logger.error(f"The ISO number specified ({iso}) is not valid")
            raise ValueError

    @property
    def shutter_speed(self):
        return self.__shutter_speed

    @shutter_speed.setter
    def shutter_speed(self, shutter_speed):
        Change the camera shutter speed

        Args:
            shutter_speed (int): shutter speed in µs
        
        logger.debug(f"Setting the shutter speed to {shutter_speed}")
        if 0 < shutter_speed < 5000:
            self.__shutter_speed = shutter_speed
            self.__picam.shutter_speed = self.__shutter_speed
            self.__wait_for_output("Change: shutter_speed")
        else:
            logger.error(f"The shutter speed specified ({shutter_speed}) is not valid")
            raise ValueError

    @property
    def exposure_mode(self):
        return self.__exposure_mode

    @exposure_mode.setter
    def exposure_mode(self, mode):
        Change the camera exposure mode

        Is one of off, auto, night, nightpreview, backlight, spotlight,
        sports, snow, beach, verylong, fixedfps, antishake, fireworks

        Args:
            mode (string): exposure mode to use
        
        logger.debug(f"Setting the exposure mode to {mode}")
        if mode in [
            "off",
            "auto",
            "night",
            "nightpreview",
            "backlight",
            "spotlight",
            "sports",
            "snow",
            "beach",
            "verylong",
            "fixedfps",
            "antishake",
            "fireworks",
        ]:
            self.__exposure_mode = mode
            self.__send_command(f"em {self.__exposure_mode}")
        else:
            logger.error(f"The exposure mode specified ({mode}) is not valid")
            raise ValueError

    @property
    def white_balance(self):
        return self.__white_balance

    @white_balance.setter
    def white_balance(self, mode):
        Change the camera white balance mode

        Is one of off, auto, sun, cloudy, shade, tungsten,
        fluorescent, incandescent, flash, horizon

        Args:
            mode (string): white balance mode to use
        
        logger.debug(f"Setting the white balance mode to {mode}")
        if mode in [
            "off",
            "auto",
            "sun",
            "cloudy",
            "shade",
            "tungsten",
            "fluorescent",
            "incandescent",
            "flash",
            "horizon",
        ]:
            self.__white_balance = mode
            self.__send_command(f"wb {self.__white_balance}")
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
        Change the camera white balance gain

            The gain value should be a int between 0 and 300. By default the camera
            is set to use 150 both for the red and the blue gain.

        Args:
            gain (tuple of int): Red gain and blue gain to use
        
        logger.debug(f"Setting the white balance mode to {gain}")
        if (0 < gain[0] < 800) and (0 < gain[1] < 800):
            self.__white_balance_gain = gain
            self.__send_command(
                f"ag {self.__white_balance_gain[0]} {self.__white_balance_gain[1]}"
            )
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
        Change the camera image gain

            The analog gain value should be an int between 100 and 1200 for the analog gain and
            between 100 and 6400 for the digital gain.
            By default the camera is set to use 1.0 both for the analog and the digital gain.

        Args:
            gain (tuple of int): Image gain to use
        
        logger.debug(f"Setting the analog gain to {gain}")
        if (100 <= gain[0] <= 1200) and (100 <= gain[1] < 6400):
            self.__image_gain = gain
            self.__send_command(f"ig {self.__image_gain[0]} {self.__image_gain[1]}")
        else:
            logger.error(f"The camera image gain specified ({gain}) is not valid")
            raise ValueError

    @property
    def image_quality(self):
        return self.__image_quality

    @image_quality.setter
    def image_quality(self, image_quality):
        Change the output image quality

        Args:
            image_quality (int): image quality [0,100]
        
        logger.debug(f"Setting image quality to {image_quality}")
        if 0 <= image_quality <= 100:
            self.__image_quality = image_quality
            self.__picam.options["quality"] = self.__image_quality
        else:
            logger.error(
                f"The output image quality specified ({image_quality}) is not valid"
            )
            raise ValueError

    @property
    def preview_quality(self):
        return self.__preview_quality

    @preview_quality.setter
    def preview_quality(self, preview_quality):
        Change the preview image quality

        Args:
            preview_quality (int): image quality [0,100]
        
        logger.debug(f"Setting preview quality to {preview_quality}")
        if 0 <= preview_quality <= 100:
            self.__preview_quality = preview_quality
            self.__send_command(f"pv {self.__preview_quality} 512 01")
        else:
            logger.error(
                f"The preview image quality specified ({preview_quality}) is not valid"
            )
            raise ValueError"""

    def capture(self, path=""):
        """Capture an image. Blocks for timeout seconds(5 by default) until the image is captured.

        Args:
            path (str, optional): Path to image file. Defaults to "".
            timeout (int, optional): Timeout duration in seconds. Defaults to 5.

        Raises:
            TimeoutError: A timeout happened before the required output showed up
        """
        logger.debug(f"Capturing an image to {path}")
        metadata = self.__picam.capture_file(path)
        time.sleep(0.1)

    def stop(self):
        """Release the camera"""
        logger.debug("Releasing the camera now")
        self.__picam.stop_recording()

    def close(self):
        """Close the camera"""
        logger.debug("Closing the camera now")
        self.__picam.close()
