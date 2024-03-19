"""imagernew provides an MQTT worker to perform stop-flow image acquisition.

This module also adjusts camera settings (but that functionality should be split off into a separate
module)."""

import datetime
import json
import multiprocessing
import os
import threading
import time
import typing

import loguru

from planktoscope import identity, integrity, mqtt
from planktoscope.imagernew import camera, mjpeg, stopflow

loguru.logger.info("planktoscope.imager is loaded")


class ImagerProcess(multiprocessing.Process):
    """An MQTT+MJPEG API for the PlanktoScope's camera and image acquisition modules."""

    # FIXME(ethanjli): instead of passing in a stop_event, just expose a `close()` method! This
    # way, we don't give any process the ability to stop all other processes watching the same
    # stop_event!
    def __init__(self, stop_event):
        """Initialize the Imager class

        Args:
            stop_event (multiprocessing.Event): shutdown event
            iso (int, optional): ISO sensitivity. Defaults to 100.
            exposure_time (int, optional): Shutter speed of the camera, default to 10000.
        """
        super().__init__(name="imager")

        loguru.logger.info("planktoscope.imager is initialising")

        # FIXME(ethanjli): move this to self._camera if only self._camera needs these settings;
        # ideally decompose config-loading to a separate module. That module should also be where
        # the file schema is defined!
        if os.path.exists("/home/pi/PlanktoScope/hardware.json"):
            # load hardware.json
            with open("/home/pi/PlanktoScope/hardware.json", "r", encoding="utf-8") as config_file:
                self._hardware_config = json.load(config_file)
                loguru.logger.debug(
                    f"Loaded hardware configuration loaded: {self._hardware_config}",
                )
        else:
            loguru.logger.info("The hardware configuration file doesn't exist, using defaults!")
            self._hardware_config = {}

        # Internal state
        self._stop_event_loop = stop_event
        self._metadata: dict[str, typing.Any] = {}
        self._routine: typing.Optional[ImageAcquisitionRoutine] = None

        # I/O
        self._mqtt: typing.Optional[mqtt.MQTT_Client] = None
        self._pump: typing.Optional[MQTTPump] = None
        self._camera: typing.Optional[MQTTCamera] = None

        loguru.logger.success("planktoscope.imager is initialized and ready to go!")

    @loguru.logger.catch
    def run(self) -> None:
        """Run the main event loop."""
        loguru.logger.info(f"The imager control thread has been started in process {os.getpid()}")
        self._mqtt = mqtt.MQTT_Client(topic="imager/#", name="imager_client")
        self._mqtt.client.publish("status/imager", '{"status":"Starting up"}')

        loguru.logger.info("Starting the pump RPC client...")
        self._pump = MQTTPump()
        self._pump.open()
        loguru.logger.success("Pump RPC client is ready!")

        loguru.logger.info("Starting the camera...")
        self._camera = MQTTCamera(self._hardware_config)
        self._camera.open()
        loguru.logger.success("Camera is ready!")
        self._mqtt.client.publish("status/imager", '{"status":"Ready"}')

        try:
            while not self._stop_event_loop.is_set():
                if self._routine is not None and not self._routine.is_alive():
                    # Garbage-collect any finished image-acquisition routine threads so that we're
                    # ready for the next configuration update command which arrives:
                    self._routine.stop()
                    self._routine = None

                if not self._mqtt.new_message_received():
                    time.sleep(0.1)
                    continue
                self._handle_new_message()
        finally:
            loguru.logger.info("Shutting down the imager process...")
            self._mqtt.client.publish("status/imager", '{"status":"Dead"}')
            self._camera.close()
            self._pump.close()
            self._mqtt.shutdown()
            loguru.logger.success("Imager process shut down! See you!")

    @loguru.logger.catch
    def _handle_new_message(self) -> None:
        """Handle a new message received over MQTT."""
        assert self._mqtt is not None
        if self._mqtt.msg is None:
            return

        loguru.logger.info("we received a new message")
        if not self._mqtt.msg["topic"].startswith("imager/"):
            loguru.logger.error(
                f"the received message was not for us! topic was {self._mqtt.msg['topic']}"
            )
            self._mqtt.read_message()
            return

        latest_message = self._mqtt.msg["payload"]
        loguru.logger.debug(latest_message)
        action = self._mqtt.msg["payload"]["action"]
        loguru.logger.debug(action)

        if action == "update_config":
            self._update_metadata(latest_message)
        elif action == "image":
            self._start_acquisition(latest_message)
        elif action == "stop" and self._routine is not None:
            self._routine.stop()
            self._routine = None
        self._mqtt.read_message()

    def _update_metadata(self, latest_message: dict[str, typing.Any]) -> None:
        """Handle a new imager command to update the configuration (i.e. the metadata)."""
        assert self._mqtt is not None

        # FIXME(ethanjli): it'll be simpler if we just take the configuration as part of the command
        # to start image acquisition!
        if self._routine is not None and self._routine.is_alive():
            loguru.logger.error("Can't update configuration during image acquisition!")
            self._mqtt.client.publish("status/imager", '{"status":"Busy"}')
            return

        if "config" not in latest_message:
            loguru.logger.error(f"Received message is missing field 'config': {latest_message}")
            self._mqtt.client.publish("status/imager", '{"status":"Configuration message error"}')
            return

        loguru.logger.info("Updating configuration...")
        self._metadata = latest_message["config"]
        self._mqtt.client.publish("status/imager", '{"status":"Config updated"}')
        loguru.logger.info("Updated configuration!")

    # FIXME(ethanjli): reorder the methods!
    def _start_acquisition(self, latest_message: dict[str, typing.Any]) -> None:
        """Handle a new imager command to start image acquisition."""
        assert self._mqtt is not None
        assert self._pump is not None
        assert self._camera is not None

        if (settings := _parse_acquisition_settings(latest_message)) is None:
            self._mqtt.client.publish("status/imager", '{"status":"Error"}')
            return

        try:
            output_path = _initialize_acquisition_directory(
                "/home/pi/data/img",
                {
                    **self._metadata,
                    "acq_local_datetime": datetime.datetime.now().isoformat().split(".")[0],
                    "acq_camera_shutter_speed": self._camera._exposure_time,
                    "acq_uuid": identity.load_machine_name(),
                    "sample_uuid": identity.load_machine_name(),
                },
            )
        except ValueError as e:
            self._mqtt.client.publish(
                "status/imager",
                json.dumps({"status": f"Configuration update error: {str(e)}"}),
            )
        if output_path is None:
            # An error status was already reported, so we don't need to do anything else
            return

        self._routine = ImageAcquisitionRoutine(
            stopflow.Routine(self._pump, self._camera.camera, output_path, settings),
            self._mqtt,
        )
        self._routine.start()


def _parse_acquisition_settings(
    latest_message: dict[str, typing.Any],
) -> typing.Optional[stopflow.Settings]:
    """Parse a command to start acquisition into stop-flow settings.

    Returns:
        A [stopflow.Settings] with the parsed settings if input validation and parsing succeeded,
        or `None` otherwise.
    """
    for field in ("nb_frame", "sleep", "volume", "pump_direction"):
        if field not in latest_message:
            loguru.logger.error(
                f"The received message is missing field '{field}': {latest_message}"
            )
            return None

    if latest_message["pump_direction"] not in stopflow.PumpDirection.__members__:
        loguru.logger.error(
            "The received message has an invalid pump direction: "
            + f"{latest_message['pump_direction']}",
        )
        return None

    try:
        return stopflow.Settings(
            total_images=int(latest_message["nb_frame"]),
            stabilization_duration=float(latest_message["sleep"]),
            pump=stopflow.DiscretePumpSettings(
                direction=stopflow.PumpDirection(latest_message.get("pump_direction", "FORWARD")),
                flowrate=float(latest_message.get("pump_flowrate", 2)),
                volume=float(latest_message["volume"]),
            ),
        )
    except ValueError:
        loguru.logger.exception("Invalid input")
        return None


def _initialize_acquisition_directory(
    base_path: str,
    metadata: dict[str, typing.Any],
) -> typing.Optional[str]:
    """Make the directory where images will be saved for the current image-acquisition routine.

    This also saves the metadata to a `metadata.json` file and initializes a file integrity log in
    the directory.

    Args:
        base_path: directory under which a subdirectory tree will be created for the image
          acquisition.
        metadata: a dict of all metadata to be associated with the acquisition. Must contain
          keys "object_date", "sample_id", and "acq_id".

    Returns:
        The directory where captured images will be saved if preparation finished successfully,
        or `None` otherwise.

    Raises:
        ValueError: Acquisition directory initialization failed.
    """
    loguru.logger.info("Setting up the directory structure for storing the pictures")

    if "object_date" not in metadata:  # needed for the directory path
        loguru.logger.error("The metadata did not contain object_date!")
        raise ValueError("object_date is missing!")

    loguru.logger.debug(f"Metadata: {metadata}")

    acq_dir_path = os.path.join(
        base_path,
        metadata["object_date"],
        str(metadata["sample_id"]).replace(" ", "_").strip("'"),
        str(metadata["acq_id"]).replace(" ", "_").strip("'"),
    )
    if os.path.exists(acq_dir_path):
        loguru.logger.error(f"Acquisition directory {acq_dir_path} already exists!")
        raise ValueError("Chosen id are already in use!")

    os.makedirs(acq_dir_path)
    loguru.logger.info("Saving metadata...")
    metadata_filepath = os.path.join(acq_dir_path, "metadata.json")
    with open(metadata_filepath, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=4)
        loguru.logger.debug(f"Saved metadata to {metadata_file}: {metadata}")
    integrity.create_integrity_file(acq_dir_path)
    integrity.append_to_integrity_file(metadata_filepath)
    return acq_dir_path


class ImageAcquisitionRoutine(threading.Thread):
    """A thread to run a single image acquisition routine to completion, with MQTT updates."""

    # TODO(ethanjli): instead of taking an arg of type mqtt.MQTT_CLIENT, just take an arg of
    # whatever `mqtt_client.client`'s type is supposed to be
    def __init__(self, routine: stopflow.Routine, mqtt_client: mqtt.MQTT_Client) -> None:
        """Initialize the thread."""
        super().__init__()
        self._routine = routine
        self._mqtt_client = mqtt_client.client

    def run(self) -> None:
        """Run a stop-flow image-acquisition routine until completion or interruption."""
        self._mqtt_client.publish("status/imager", '{"status":"Started"}')
        while True:
            if (result := self._routine.run_step()) is None:
                if self._routine.interrupted:
                    loguru.logger.debug("Image-acquisition routine was interrupted!")
                    self._mqtt_client.publish("status/imager", '{"status":"Interrupted"}')
                    break
                loguru.logger.debug("Image-acquisition routine ran to completion!")
                self._mqtt_client.publish("status/imager", '{"status":"Done"}')
                break

            index, filename = result
            filename_path = os.path.join(self._routine.output_path, filename)
            try:
                integrity.append_to_integrity_file(filename_path)
            except FileNotFoundError:
                self._mqtt_client.publish(
                    "status/imager",
                    f'{{"status":"Image {index + 1}/{self._routine.settings.total_images} '
                    + 'WAS NOT CAPTURED! STOPPING THE PROCESS!"}}',
                )
                break

            self._mqtt_client.publish(
                "status/imager",
                f'{{"status":"Image {index + 1}/{self._routine.settings.total_images} '
                + f'saved to {filename}"}}',
            )

    def stop(self) -> None:
        """Stop the thread.

        Blocks until the thread is done.

        Raises:
            RuntimeError: this method was called before the thread was started.
        """
        self._routine.stop()
        self.join()


# TODO(ethanjli): rearchitect the hardware controller so that the imager can directly call pump
# methods (by running all modules in the same process), so that we can just delete this entire class
# and simplify function calls between the imager and the pump!
class MQTTPump:
    """Thread-safe RPC stub for remotely controlling the pump over MQTT."""

    def __init__(self) -> None:
        """Initialize the stub."""
        # Note(ethanjli): We have to have our own MQTT client because we need to publish messages
        # from a separate thread, and currently the MQTT client isn't thread-safe (it deadlocks
        # if we don't have a separate MQTT client):
        self._mqtt: typing.Optional[mqtt.MQTT_Client] = None
        self._mqtt_receiver_thread: typing.Optional[threading.Thread] = None
        self._mqtt_receiver_close = threading.Event()  # close() was called
        self._done = threading.Event()  # run_discrete() finished or stop() was called
        self._discrete_run = threading.Lock()  # mutex on starting the pump

    def open(self) -> None:
        """Start the pump MQTT client.

        Launches a thread to listen for MQTT updates from the pump. After this method is called,
        the `run_discrete()` and `stop()` methods can be called.
        """
        if self._mqtt is not None:
            return

        self._mqtt = mqtt.MQTT_Client(topic="status/pump", name="imager_pump_client")
        self._mqtt_receiver_thread = threading.Thread(target=self._receive_messages)
        self._mqtt_receiver_thread.start()

    def _receive_messages(self) -> None:
        """Update internal state based on pump status updates received over MQTT."""
        assert self._mqtt is not None

        while not self._mqtt_receiver_close.is_set():
            if not self._mqtt.new_message_received():
                time.sleep(0.1)
                continue
            if self._mqtt.msg is None or self._mqtt.msg["topic"] != "status/pump":
                continue

            if self._mqtt.msg["payload"]["status"] not in {"Done", "Interrupted"}:
                loguru.logger.debug(f"Ignoring pump status update: {self._mqtt.msg['payload']}")
                self._mqtt.read_message()
                continue

            loguru.logger.debug(f"The pump has stopped: {self._mqtt.msg['payload']}")
            self._mqtt.client.unsubscribe("status/pump")
            self._mqtt.read_message()
            self._done.set()
            if self._discrete_run.locked():
                self._discrete_run.release()

    def run_discrete(self, settings: stopflow.DiscretePumpSettings) -> None:
        """Run the pump for a discrete volume at the specified flow rate and direction.

        Blocks until the pump has finished pumping. Before starting the pump, this first blocks
        until the previous `run_discrete()` call (if it was started in another thread and is still
        running) has finished.

        Raises:
            RuntimeError: this method was called before the `open()` method was called, or after
              the `close()` method was called.
        """
        if self._mqtt is None:
            raise RuntimeError("MQTT client was not initialized yet!")

        # We ignore the pylint error here because the lock can only be released from a different
        # thread (the thread which calls the `handle_status_update()` method):
        self._discrete_run.acquire()  # pylint: disable=consider-using-with
        self._done.clear()
        self._mqtt.client.subscribe("status/pump")
        self._mqtt.client.publish(
            "actuator/pump",
            json.dumps(
                {
                    "action": "move",
                    "direction": settings.direction.value,
                    "flowrate": settings.flowrate,
                    "volume": settings.volume,
                }
            ),
        )
        self._done.wait()

    def stop(self) -> None:
        """Stop the pump."""
        if self._mqtt is None:
            raise RuntimeError("MQTT client was not initialized yet!")

        self._mqtt.client.subscribe("status/pump")
        self._mqtt.client.publish("actuator/pump", '{"action": "stop"}')

    def close(self) -> None:
        """Close the pump MQTT client, if it's currently open.

        Stops the MQTT receiver thread and blocks until it finishes. After this method is called,
        no methods are allowed to be called.
        """
        if self._mqtt is None:
            return

        self._mqtt_receiver_close.set()
        if self._mqtt_receiver_thread is not None:
            self._mqtt_receiver_thread.join()
        self._mqtt_receiver_thread = None
        self._mqtt.shutdown()
        self._mqtt = None

        # We don't know if the run is done (or if it'll ever finish), but we'll release the lock to
        # prevent deadlocks:
        if not self._discrete_run.locked():
            return
        self._discrete_run.release()


# TODO(ethanjli): split off the camera MQTT API into a separate subpackage, and also move the
# picamera2 wrapper into that subpackage
class MQTTCamera:
    """Camera with MQTT API for adjusting camera settings.

    Attribs:
        camera: the underlying camera exposed by this MQTT API.
    """

    def __init__(
        self,
        hardware_config: dict[str, typing.Any],
        # FIXME(ethanjli): handle exposure time and ISO in hardware config instead of keyword args!
        # exposure_time: int = 125,
        exposure_time: int = 15000,
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
        self.__exposure_mode: camera.ExposureModes = "normal"  # FIXME(ethanjli): default to "off"?
        self.__white_balance_mode: camera.WhiteBalanceModes = "off"
        self.__white_balance_gain = (
            hardware_config.get("red_gain", 2.00),
            hardware_config.get("blue_gain", 1.40),
        )
        self.__image_gain = hardware_config.get("analog_gain", 1.00)

        # I/O
        self._preview_stream: camera.PreviewStream = camera.PreviewStream()
        self.camera: camera.PiCamera = camera.PiCamera(self._preview_stream)
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
            if (message := self._mqtt.msg) is None or message["topic"] != "imager/image":
                continue
            if message["payload"].get("action", "") != "settings":
                continue
            if "settings" not in message["payload"]:
                loguru.logger.error(f"Received message is missing field 'settings': {message}")
                self._mqtt.client.publish("status/imager", '{"status":"Camera settings error"}')
                continue

            loguru.logger.info("Updating camera settings...")
            settings = message["payload"]["settings"]
            self._mqtt.read_message()
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
                continue

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
        loguru.logger.debug(
            f"updating the camera white balance gain to {self.__white_balance_gain}"
        )
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

        loguru.logger.debug(
            f"updating the camera white balance mode to {settings['white_balance']}"
        )
        self.__white_balance_mode = settings.get("white_balance", self.__white_balance_mode)
        loguru.logger.debug(
            f"updating the camera white balance mode to {self.__white_balance_mode}"
        )
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
            loguru.logger.debug(
                f"updating the camera image analog gain to {settings['image_gain']}"
            )
            self.__image_gain = settings["image_gain"].get("analog", self.__image_gain)
        loguru.logger.debug(f"updating the camera image gain to {self.__image_gain}")
        try:
            self.camera.controls.image_gain = self.__image_gain
        except ValueError as e:
            loguru.logger.error("the requested image gain is not valid!")
            self._mqtt.client.publish(
                "status/imager",
                '{"status":"Error: Image gain not valid"}',
            )
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
