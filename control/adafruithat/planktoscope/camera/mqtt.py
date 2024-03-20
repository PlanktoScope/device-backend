"""mqtt provides an MJPEG+MQTT API for camera interaction."""

import json
import os
import threading
import time
import typing

import loguru

from planktoscope import mqtt
from planktoscope.camera import hardware, mjpeg

loguru.logger.info("planktoscope.imager is loaded")


# FIXME(ethanjli): simplify this class
class Worker:
    """Runs a camera with live MJPEG preview and an MQTT API for adjusting camera settings.

    Attribs:
        camera: the underlying camera exposed by this MQTT API.
    """

    def __init__(self, mjpeg_server_address: tuple[str, int] = ("", 8000)) -> None:
        """Initialize the backend.

        Args:
            mqtt_client: an MQTT client.
            exposure_time: the default value for initializing the camera's exposure time.

        Raises:
            ValueError: one or more values in the hardware config file are of the wrong type.
        """
        # Settings
        # FIXME(ethanjli): decompose config-loading to a separate module. That module should also be
        # where the file schema is defined!
        if os.path.exists("/home/pi/PlanktoScope/hardware.json"):
            # load hardware.json
            with open("/home/pi/PlanktoScope/hardware.json", "r", encoding="utf-8") as config_file:
                hardware_config = json.load(config_file)
                loguru.logger.debug(
                    f"Loaded hardware configuration loaded: {hardware_config}",
                )
        else:
            loguru.logger.info("The hardware configuration file doesn't exist, using defaults!")
            hardware_config = {}

        self._settings = hardware.SettingsValues(
            auto_exposure=False,
            exposure_time=125,  # the default (minimum) exposure time in the PlanktoScope GUI
            image_gain=hardware_config.get("analog_gain", 1.00),  # the default ISO of 100
            brightness=0.0,  # the default "normal" brightness
            contrast=1.0,  # the default "normal" contrast
            auto_white_balance=False,  # the default setting in the PlanktoScope GUI
            white_balance_gains=hardware.WhiteBalanceGains(
                # the default gains from the default v2.5 hardware config
                red=float(hardware_config.get("red_gain", 2.4)),
                blue=float(hardware_config.get("blue_gain", 1.35)),
            ),
            sharpness=0,  # disable the default "normal" sharpening level
            jpeg_quality=80,  # trade off between image file size and quality
        )

        # I/O
        self._preview_stream: hardware.PreviewStream = hardware.PreviewStream()
        self.camera: hardware.PiCamera = hardware.PiCamera(self._preview_stream)
        # Note(ethanjli): if we wanted to allow re-opening the worker after calling the `close()`
        # method, we would need to initialize the streaming server & thread in the `open()` method.
        # But we don't need to do that, so it's simpler to initialize them here rather than
        # initializing them as `None` (and giving them `typing.Optional` types):
        self._streaming_server = mjpeg.StreamingServer(self._preview_stream, mjpeg_server_address)
        self._streaming_thread = threading.Thread(target=self._streaming_server.serve_forever)
        # TODO(ethanjli): allow initializing the MQTT client without it immediately trying to
        # connect to the message broker (i.e. add an `open()` method to the client!):
        self._mqtt: typing.Optional[mqtt.MQTT_Client] = None
        # Note(ethanjli): if we need to reduce the number of object members, we could convert this
        # class to subclass threading.Thread, and just bring up (and tear down) some stuff in the
        # `run()` method.
        self._mqtt_receiver_thread: typing.Optional[threading.Thread] = None
        self._mqtt_receiver_close = threading.Event()  # close() was called

    @loguru.logger.catch
    def open(self) -> None:
        """Start the camera and MJPEG preview stream."""
        loguru.logger.info("Initializing the camera with default settings...")
        self.camera.open()
        self.camera.settings = self._settings

        loguru.logger.info("Starting the MJPEG streaming server...")
        self._streaming_thread.start()

        loguru.logger.info("Starting the MQTT backend...")
        # TODO(ethanjli): expose the camera settings over "camera/settings" instead! This requires
        # removing the "settings" action from the "imager/image" route which is a breaking change
        # to the MQTT API, so we'll do this later.
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
        assert self._mqtt is not None

        if message["topic"] != "imager/image" or message["payload"].get("action", "") != "settings":
            return
        if "settings" not in message["payload"]:
            loguru.logger.error(f"Received message is missing field 'settings': {message}")
            self._mqtt.client.publish("status/imager", '{"status":"Camera settings error"}')
            return

        loguru.logger.info("Updating camera settings...")
        settings = message["payload"]["settings"]
        try:
            converted_settings = _convert_settings(
                settings, self.camera.settings.white_balance_gains
            )
            _validate_settings(converted_settings)
        except ValueError as e:
            loguru.logger.exception(
                f"Couldn't convert MQTT command to hardware settings: {settings}",
            )
            self._mqtt.client.publish("status/imager", json.dumps({"status": f"Error: {str(e)}"}))
            return

        self.camera.settings = converted_settings
        self._mqtt.client.publish("status/imager", '{"status":"Camera settings updated"}')
        loguru.logger.info("Updated camera settings!")

    # TODO(ethanjli): allow an MQTT client to trigger this method with an MQTT command. This
    # requires modifying the MQTT API (by adding a new route), and we'll want to make the Node-RED
    # dashboard query that route at startup, so we'll do this later.
    def _announce_camera_name(self) -> None:
        """Announce the camera's sensor name as a status update."""
        assert self._mqtt is not None

        camera_names = {
            "IMX219": "Camera v2.1",
            "IMX477": "Camera HQ",
        }
        self._mqtt.client.publish(
            "status/imager",
            json.dumps(
                {"camera_name": camera_names.get(self.camera.sensor_name, "Not recognized")}
            ),
        )

    def close(self) -> None:
        """Close the camera, if it's currently open.

        Stops the MQTTT receiver thread, the MJPEG streaming server, and the camera and block until
        they all finish. After this method is called, the worker should be destroyed - no other
        methods may be called.
        """
        if self._mqtt is not None:
            loguru.logger.info("Stopping the MQTT API...")
            self._mqtt_receiver_close.set()
            if self._mqtt_receiver_thread is not None:
                self._mqtt_receiver_thread.join()
            self._mqtt_receiver_thread = None
            self._mqtt.shutdown()
            self._mqtt = None

        loguru.logger.info("Stopping the MJPEG streaming server...")
        self._streaming_server.shutdown()
        self._streaming_server.server_close()
        self._streaming_thread.join()

        loguru.logger.info("Stopping the camera...")
        self.camera.close()


def _convert_settings(
    command_settings: dict[str, typing.Any],
    default_white_balance_gains: typing.Optional[hardware.WhiteBalanceGains],
) -> hardware.SettingsValues:
    """Convert MQTT command settings to camera hardware settings.

    Args:
        command_settings: the settings to convert.
        default_white_balance_gains: white-balance gains to substitute for missing values if exactly
          one gain is provided.

    Raises:
        ValueError: at least one of the MQTT command settings is invalid.
    """
    # TODO(ethanjli): separate out the status from the error message in the MQTT API, so
    # that we can just directly use the error messages from the
    # `hardware.SettingsValues.validate()` method. That would be simpler; for now we're
    # trying to keep the MQTT API unchanged, so we return different ValueErrors.
    converted = hardware.SettingsValues()
    if "shutter_speed" in command_settings:
        try:
            exposure_time = int(command_settings["shutter_speed"])
        except ValueError as e:
            raise ValueError("Shutter speed not valid") from e
        converted = converted._replace(exposure_time=exposure_time)
    converted = converted.overlay(_convert_image_gain_settings(command_settings))
    if "white_balance" in command_settings:
        if (awb := command_settings["white_balance"]) not in {"auto", "off"}:
            raise ValueError("White balance mode {awb} not valid")
        converted = converted._replace(auto_white_balance=awb != "off")
    converted = converted.overlay(
        _convert_white_balance_gain_settings(command_settings, default_white_balance_gains)
    )

    return converted


def _convert_image_gain_settings(
    command_settings: dict[str, typing.Any],
) -> hardware.SettingsValues:
    """Convert image gains in MQTT command settings to camera hardware settings.

    Args:
        command_settings: the settings to convert.

    Raises:
        ValueError: at least one of the MQTT command settings is invalid.
    """
    converted = hardware.SettingsValues()
    # TODO(ethanjli): now that we're using image_gain as the ISO, we should remove one of them
    # from the MQTT API (it could be better to keep ISO since it's tied to metadata, or it could
    # be better to remove ISO since ISO is a fictitious parameter (since the hardware doesn't
    # actually have ISO) and needs a conversion to the real image_gain parameter for the hardware).
    # Then we could delete this function. For now, we'll just redirect both to image_gain, with ISO
    # taking precedence when both are provided in the same command:
    if "image_gain" in command_settings:
        try:
            image_gain = float(command_settings["image_gain"]["analog"])
        except (ValueError, KeyError) as e:
            raise ValueError("Image gain not valid") from e
        converted = converted._replace(image_gain=image_gain)
    if "iso" in command_settings:
        try:
            iso = float(command_settings["iso"])
        except ValueError as e:
            raise ValueError("Iso number not valid") from e
        converted = converted._replace(image_gain=iso / 100)

    return converted


def _convert_white_balance_gain_settings(
    command_settings: dict[str, typing.Any],
    # TODO(ethanjli): modify the PlanktoScope GUI to send both red and blue white balance values
    # together each time either is updated, and modify the MQTT API to require both values to be
    # always provided together. That would simplify the code here and remove the need to keep track
    # of previous white balance gains (which could be prone to getting into an inconsistent state
    # compared to the values shown in the GUI); we could maybe even delete this function afterwards.
    default_white_balance_gains: typing.Optional[hardware.WhiteBalanceGains],
) -> hardware.SettingsValues:
    """Convert white-balance gains in MQTT command settings to camera hardware settings.

    Args:
        command_settings: the settings to convert.
        default_white_balance_gains: white-balance gains to substitute for missing values if exactly
          one gain is provided.

    Raises:
        ValueError: at least one of the MQTT command settings is invalid.
    """
    converted = hardware.SettingsValues()
    if "white_balance_gain" not in command_settings:
        return converted

    # FIXME(ethanjli): use normal white-balance gains instead of the gains which are
    # multiplied by 100 in the MQTT API, since the PlanktoScope GUI shows them without
    # the multiplication by 100 anyways
    try:
        red_gain = float(command_settings["white_balance_gain"]["red"]) / 100
    except ValueError as e:
        raise ValueError("White balance gain not valid") from e
    except KeyError as e:
        if default_white_balance_gains is None:
            raise ValueError("White balance gain not valid") from e
        red_gain = default_white_balance_gains.red
    try:
        blue_gain = float(command_settings["white_balance_gain"]["blue"]) / 100
    except ValueError as e:
        raise ValueError("White balance gain not valid") from e
    except KeyError as e:
        if default_white_balance_gains is None:
            raise ValueError("White balance gain not valid") from e
        blue_gain = default_white_balance_gains.blue
    return converted._replace(
        white_balance_gains=hardware.WhiteBalanceGains(red=red_gain, blue=blue_gain)
    )


# TODO(ethanjli): separate out the status from the error message in the MQTT API, so
# that we can just directly use the error messages from the
# `hardware.SettingsValues.validate()` method, and then we can delete this function. That would be
# simpler; for now we're trying to keep the MQTT API unchanged, so we have this wrapper to return
# different ValueErrors.
def _validate_settings(settings: hardware.SettingsValues) -> None:
    """Check validity of camera hardware settings.

    Raises:
        ValueError: at least one of the MQTT command settings is invalid.
    """
    if validation_errors := settings.validate():
        loguru.logger.error(
            f"Invalid camera settings requested: {'; '.join(validation_errors)}",
        )
        erroneous_field, _ = validation_errors[0].split(" out of range", 1)
        error_message_mappings = {
            "Exposure time": "Shutter speed",
            "Image gain": "Iso number",
            "Red white-balance gain": "White balance gain",
            "Blue white-balance gain": "White balance gain",
        }
        raise ValueError(
            f"{error_message_mappings.get(erroneous_field, erroneous_field)} not valid",
        )
