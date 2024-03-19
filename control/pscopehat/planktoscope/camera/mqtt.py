"""mqtt provides an MJPEG+MQTT API for camera interaction."""

import json
import threading
import time
import typing

import loguru

from planktoscope import mqtt
from planktoscope.camera import hardware, mjpeg

loguru.logger.info("planktoscope.imager is loaded")


class Worker:
    """Runs a camera with live MJPEG preview and an MQTT API for adjusting camera settings.

    Attribs:
        camera: the underlying camera exposed by this MQTT API.
    """

    def __init__(
        self,
        hardware_config: dict[str, typing.Any],
        # FIXME(ethanjli): handle exposure time and ISO in hardware config instead of keyword args!
        exposure_time: int = 125,
        # exposure_time: int = 15000,
        # iso: int = 100,
    ) -> None:
        """Initialize the backend.

        Args:
            hardware_config: a dict of camera control settings.
            mqtt_client: an MQTT client.
            exposure_time: the default value for initializing the camera's exposure time.
        """
        # Settings
        # self.__camera_type = hardware_config.get("camera_type", "v2.1")
        # self.__resolution = None  # this is set by the start method
        # FIXME: consolidate all camera settings into a dataclass or namedtuple!
        # self.__iso = iso
        self._exposure_time = exposure_time  # FIXME(ethanjli): load from hardware config?
        self.__exposure_mode: hardware.ExposureModes = (
            "normal"  # FIXME(ethanjli): default to "off"?
        )
        self.__white_balance_mode: hardware.WhiteBalanceModes = "off"
        self.__white_balance_gain = (
            hardware_config.get("red_gain", 2.00),
            hardware_config.get("blue_gain", 1.40),
        )
        self.__image_gain = hardware_config.get("analog_gain", 1.00)

        # I/O
        self._preview_stream: hardware.PreviewStream = hardware.PreviewStream()
        self.camera: hardware.PiCamera = hardware.PiCamera(self._preview_stream)
        self._streaming_server: typing.Optional[mjpeg.StreamingServer] = None
        self._streaming_thread: typing.Optional[threading.Thread] = None
        self._mqtt: typing.Optional[mqtt.MQTT_Client] = None
        self._mqtt_receiver_thread: typing.Optional[threading.Thread] = None
        self._mqtt_receiver_close = threading.Event()  # close() was called

    def open(self) -> None:
        """Start the camera and MJPEG preview stream."""
        loguru.logger.info("Initializing the camera with default settings...")
        self.camera.open()
        if self.camera.controls is None:
            raise RuntimeError("Camera was unable to start")

        # FIXME(ethanjli): simplify initialization of camera settings
        time.sleep(0.1)
        self.camera.controls.exposure_time = self._exposure_time
        time.sleep(0.1)
        self.camera.controls.exposure_mode = self.__exposure_mode
        time.sleep(0.1)
        self.camera.controls.white_balance_mode = self.__white_balance_mode
        time.sleep(0.1)
        self.camera.controls.white_balance_gains = self.__white_balance_gain
        time.sleep(0.1)
        self.camera.controls.image_gain = self.__image_gain

        # if self.camera.sensor_name == "IMX219":  # Camera v2.1
        #     self.__resolution = (3280, 2464)
        # elif self.camera.sensor_name == "IMX477":  # Camera HQ
        #     self.__resolution = (4056, 3040)
        # else:
        #     self.__resolution = (1280, 1024)
        #     loguru.logger.error(
        #         f"The connected camera {self.camera.sensor_name} is not recognized, "
        #         + "please check your camera"
        #     )

        loguru.logger.info("Starting the MJPEG streaming server...")
        address = ("", 8000)  # FIXME(ethanjli): parameterize this
        self._streaming_server = mjpeg.StreamingServer(self._preview_stream, address)
        # FIXME(ethanjli): make this not be a daemon thread, by recovering resourcse
        # appropriately at shutdown!
        self._streaming_thread = threading.Thread(target=self._streaming_server.serve_forever)
        self._streaming_thread.start()

        loguru.logger.info("Starting the MQTT backend...")
        # FIXME(ethanjli): expose the camera settings over "camera/settings" instead!
        self._mqtt = mqtt.MQTT_Client(topic="imager/image", name="imager_camera_client")
        self._mqtt_receiver_close.clear()
        self._mqtt_receiver_thread = threading.Thread(target=self._receive_messages)
        self._mqtt_receiver_thread.start()
        self._announce_camera_name()

    def _receive_messages(self) -> None:
        """Update internal state based on pump status updates received over MQTT."""
        assert self._mqtt is not None

        while not self._mqtt_receiver_close.is_set():
            if not self._mqtt.new_message_received():
                time.sleep(0.1)
                continue
            loguru.logger.debug(self._mqtt.msg)
            if (message := self._mqtt.msg) is None:
                continue
            self._receive_message(message)
            self._mqtt.read_message()

    def _receive_message(self, message: dict[str, typing.Any]) -> None:
        """Handle a single MQTT message."""
        if message["topic"] != "imager/image" or message["payload"].get("action", "") != "settings":
            return
        if "settings" not in message["payload"]:
            loguru.logger.error(f"Received message is missing field 'settings': {message}")
            self._mqtt.client.publish("status/imager", '{"status":"Camera settings error"}')
            return

        loguru.logger.info("Updating camera settings...")
        settings = message["payload"]["settings"]
        try:
            # FIXME(ethanjli): consolidate these into a single camera controls validation step
            # and a single update step, e.g. using a dataclass
            if "shutter_speed" in settings:
                self.__message_settings_ss(settings)
            if "white_balance_gain" in settings:
                self.__message_settings_wb_gain(settings)
            if "white_balance" in settings:
                self.__message_settings_wb(settings)
            if "image_gain" in settings:
                self.__message_settings_image_gain(settings)
        except ValueError:
            # the methods above already returned an error response if an error occurred, in
            # which case we don't want to send a success response
            return

        self._mqtt.client.publish("status/imager", '{"status":"Camera settings updated"}')
        loguru.logger.info("Updated camera settings!")

    def __message_settings_ss(self, settings):
        assert self._mqtt is not None

        if self.camera.controls is None:
            raise RuntimeError("camera has not started yet")

        self._exposure_time = settings.get("shutter_speed", self._exposure_time)
        loguru.logger.debug(f"updating the camera shutter speed to {self._exposure_time}")
        try:
            self.camera.controls.exposure_time = self._exposure_time
        except ValueError as e:
            loguru.logger.error("the requested shutter speed is not valid!")
            self._mqtt.client.publish(
                "status/imager", '{"status":"Error: Shutter speed not valid"}'
            )
            raise e

    def __message_settings_wb_gain(self, settings):
        assert self._mqtt is not None
        if self.camera.controls is None:
            raise RuntimeError("camera has not started yet")

        # fixme: use normal white-balance gains instead of the gains which are multiplied by 100 in
        # the mqtt api

        if "red" in settings["white_balance_gain"]:
            red_gain = settings["white_balance_gain"]["red"] / 100
            loguru.logger.debug(f"updating the camera white balance red gain to {red_gain}")
            self.__white_balance_gain = (red_gain, self.__white_balance_gain[1])
        if "blue" in settings["white_balance_gain"]:
            blue_gain = settings["white_balance_gain"]["blue"] / 100
            loguru.logger.debug(f"updating the camera white balance blue gain to {blue_gain}")
            self.__white_balance_gain = (self.__white_balance_gain[0], blue_gain)
        loguru.logger.debug(f"updating white balance gain to {self.__white_balance_gain}...")
        try:
            self.camera.controls.white_balance_gains = self.__white_balance_gain
        except ValueError as e:
            loguru.logger.error("the requested white balance gain is not valid!")
            self._mqtt.client.publish(
                "status/imager",
                '{"status":"Error: White balance gain not valid"}',
            )
            raise e

    def __message_settings_wb(self, settings):
        assert self._mqtt is not None
        if self.camera.controls is None:
            raise RuntimeError("camera has not started yet")

        loguru.logger.debug(f"updating white balance mode to {settings['white_balance']}...")
        self.__white_balance_mode = settings.get("white_balance", self.__white_balance_mode)
        try:
            self.camera.controls.white_balance_mode = self.__white_balance_mode
        except ValueError as e:
            loguru.logger.error("the requested white balance is not valid!")
            self._mqtt.client.publish(
                "status/imager",
                f'{"status":"Error: Invalid white balance mode {self.__white_balance_mode}"}',
            )
            raise e

    def __message_settings_image_gain(self, settings):
        assert self._mqtt is not None
        if self.camera.controls is None:
            raise RuntimeError("camera has not started yet")

        if "analog" in settings["image_gain"]:
            loguru.logger.debug(f"updating image gain to {settings['image_gain']}")
            self.__image_gain = settings["image_gain"].get("analog", self.__image_gain)
        loguru.logger.debug(f"updating the camera image gain to {self.__image_gain}")
        try:
            self.camera.controls.image_gain = self.__image_gain
        except ValueError as e:
            loguru.logger.error("the requested image gain is not valid!")
            self._mqtt.client.publish("status/imager", '{"status":"Error: Image gain not valid"}')
            raise e

    # TODO(ethanjli): allow an MQTT client to trigger this method with an MQTT command
    def _announce_camera_name(self) -> None:
        """Announce the camera's sensor name as a status update."""
        assert self._mqtt is not None
        assert self.camera.controls is not None

        camera_names = {
            "IMX219": "Camera v2.1",
            "IMX477": "Camera HQ",
        }
        self._mqtt.client.publish(
            "status/imager",
            json.dumps(
                {
                    "camera_name": camera_names.get(
                        self.camera.controls.sensor_name, "Not recognized"
                    ),
                }
            ),
        )

    def close(self) -> None:
        """Close the camera, if it's currently open.

        Stops the MQTTT receiver thread, the MJPEG streaming server, and the camera and block until
        they all finish.
        """
        if self._mqtt is not None:
            loguru.logger.info("Stopping the MQTT API...")
            self._mqtt_receiver_close.set()
            if self._mqtt_receiver_thread is not None:
                self._mqtt_receiver_thread.join()
            self._mqtt_receiver_thread = None
            self._mqtt.shutdown()
            self._mqtt = None

        if self._streaming_server is not None:
            loguru.logger.info("Stopping the MJPEG streaming server...")
            self._streaming_server.shutdown()
            self._streaming_server.server_close()
            if self._streaming_thread is not None:
                self._streaming_thread.join()
                self._streaming_thread = None
            self._streaming_server = None

        loguru.logger.info("Stopping the camera...")
        self.camera.close()
