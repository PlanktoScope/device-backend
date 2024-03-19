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
    """This class contains the main definitions for the imager of the PlanktoScope"""

    # def __init__(self, stop_event, exposure_time=125, iso=100):
    def __init__(self, stop_event, exposure_time=125):
        """Initialize the Imager class

        Args:
            stop_event (multiprocessing.Event): shutdown event
            iso (int, optional): ISO sensitivity. Defaults to 100.
            exposure_time (int, optional): Shutter speed of the camera, default to 10000.
        """
        super().__init__(name="imager")

        loguru.logger.info("planktoscope.imager is initialising")

        if os.path.exists("/home/pi/PlanktoScope/hardware.json"):
            # load hardware.json
            with open("/home/pi/PlanktoScope/hardware.json", "r", encoding="utf-8") as config_file:
                configuration = json.load(config_file)
                loguru.logger.debug(f"Hardware configuration loaded is {configuration}")
        else:
            loguru.logger.info("The hardware configuration file doesn't exists, using defaults")
            configuration = {}

        # self.__camera_type = configuration.get("camera_type", "v2.1")

        self._streaming_thread = None
        # self.shutdown_event = threading.Event()
        self.stop_event = stop_event

        self._mqtt = None

        self._pump: typing.Optional[MQTTPump] = None
        self._routine: typing.Optional[ImageAcquisitionRoutine] = None

        # Camera
        self.preview_stream = camera.PreviewStream()
        self._camera = camera.PiCamera(self.preview_stream)
        # self.__resolution = None  # this is set by the start method
        # self.__iso = iso
        self.__exposure_time = exposure_time
        self.__exposure_mode: camera.ExposureModes = "normal"  # FIXME(ethanjli): default to "off"?
        self.__white_balance_mode: camera.WhiteBalanceModes = "off"
        self.__white_balance_gain = (
            configuration.get("red_gain", 2.00),
            configuration.get("blue_gain", 1.40),
        )
        self.__image_gain = configuration.get("analog_gain", 1.00)

        self.__base_path = "/home/pi/data/img"
        if not os.path.exists(self.__base_path):
            os.makedirs(self.__base_path)

        self.__global_metadata = {}

        loguru.logger.success("planktoscope.imager is initialised and ready to go!")

    def _start_acquisition(self, latest_message):
        """Actions for when we receive a message"""
        if self._mqtt is None:
            raise RuntimeError("Imager MQTT client is not running yet")
        if self._pump is None:
            raise RuntimeError("Pump RPC client was not initialized yet")

        # Process command args
        for field in ("nb_frame", "sleep", "volume", "pump_direction"):
            if field not in latest_message:
                loguru.logger.error(
                    f"The received message is missing field '{field}': {latest_message}"
                )
                self._mqtt.client.publish("status/imager", '{"status":"Error"}')
                return
        if latest_message["pump_direction"] not in stopflow.PumpDirection.__members__:
            loguru.logger.error(
                "The received message has an invalid pump direction: "
                + f"{latest_message['pump_direction']}",
            )
            self._mqtt.client.publish("status/imager", '{"status":"Error"}')
            return
        # TODO(ethanjli): add input validation to prevent an argument which doesn't convert to
        # int/float from crashing the backend!
        settings = stopflow.Settings(
            total_images=int(latest_message["nb_frame"]),
            stabilization_duration=float(latest_message["sleep"]),
            pump=stopflow.DiscretePumpSettings(
                direction=stopflow.PumpDirection(latest_message.get("pump_direction", "FORWARD")),
                flowrate=float(latest_message.get("pump_flowrate", 2)),
                volume=float(latest_message["volume"]),
            ),
        )

        # Add/update some metadata fields
        output_path = self._initialize_acquisition_directory(
            metadata={
                **self.__global_metadata,
                "acq_local_datetime": datetime.datetime.now().isoformat().split(".")[0],
                "acq_camera_shutter_speed": self.__exposure_time,
                "acq_uuid": identity.load_machine_name(),
                "sample_uuid": identity.load_machine_name(),
            }
        )
        if output_path is None:
            # An error status was already reported, so we don't need to do anything else
            return

        self._routine = ImageAcquisitionRoutine(
            stopflow.Routine(
                self._pump,
                self._camera,
                output_path,
                settings,
            ),
            self._mqtt,
        )
        self._routine.start()

    def _initialize_acquisition_directory(
        self,
        metadata: dict[str, typing.Any],
    ) -> typing.Optional[str]:
        """Make the directory where images will be saved for the current image-acquisition routine.

        Args:
            metadata: a dict of all metadata to be associated with the acquisition. Must contain
              keys "object_date", "sample_id", and "acq_id".

        Returns:
            The directory where captured images will be saved if preparation finished successfully,
            or `None` otherwise.

        Raises:
            RuntimeError: this method was called before the MQTT client was started.
        """
        if self._mqtt is None:
            raise RuntimeError("Imager MQTT client is not running yet")

        loguru.logger.info("Setting up the directory structure for storing the pictures")

        if "object_date" not in metadata:  # needed for the directory path
            loguru.logger.error("The metadata did not contain object_date!")
            self._mqtt.client.publish(
                "status/imager",
                '{"status":"Configuration update error: object_date is missing!"}',
            )
            return None

        loguru.logger.debug(f"Metadata: {metadata}")

        acq_dir_path = os.path.join(
            self.__base_path,
            metadata["object_date"],
            str(metadata["sample_id"]).replace(" ", "_").strip("'"),
            str(metadata["acq_id"]).replace(" ", "_").strip("'"),
        )
        if os.path.exists(acq_dir_path):
            loguru.logger.error(f"Acquisition directory {acq_dir_path} already exists!")
            self._mqtt.client.publish(
                "status/imager",
                '{"status":"Configuration update error: Chosen id are already in use!"}',
            )
            return None

        os.makedirs(acq_dir_path)
        loguru.logger.info("Saving metadata...")
        metadata_filepath = os.path.join(acq_dir_path, "metadata.json")
        with open(metadata_filepath, "w", encoding="utf-8") as metadata_file:
            json.dump(metadata, metadata_file, indent=4)
            loguru.logger.debug(f"Saved metadata to {metadata_file}: {metadata}")
        integrity.create_integrity_file(acq_dir_path)
        integrity.append_to_integrity_file(metadata_filepath)
        return acq_dir_path

    # copied #
    def __message_update(self, latest_message):
        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")

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
        self.__global_metadata = latest_message["config"]
        self._mqtt.client.publish("status/imager", '{"status":"Config updated"}')
        loguru.logger.info("Updated configuration!")

    def __message_settings(self, latest_message):
        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")

        if self._routine is not None:
            loguru.logger.error("Can't update the camera settings during image acquisition!")
            self._mqtt.client.publish("status/imager", '{"status":"Busy"}')
            return

        if "settings" not in latest_message:
            loguru.logger.error(f"Received message is missing field 'settings': {latest_message}")
            self._mqtt.client.publish("status/imager", '{"status":"Camera settings error"}')
            return

        loguru.logger.info("Updating camera settings...")
        settings = latest_message["settings"]

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
            # the methods above already returned an error response if an error occurred, in which
            # case we don't want to send a success response
            return

        self._mqtt.client.publish("status/imager", '{"status":"Camera settings updated"}')
        loguru.logger.info("Updated camera settings!")

    def __message_settings_ss(self, settings):
        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")
        if self._camera.controls is None:
            raise RuntimeError("camera has not started yet")

        self.__exposure_time = settings.get("shutter_speed", self.__exposure_time)
        loguru.logger.debug(f"updating the camera shutter speed to {self.__exposure_time}")
        try:
            self._camera.controls.exposure_time = self.__exposure_time
        except ValueError as e:
            loguru.logger.error("the requested shutter speed is not valid!")
            self._mqtt.client.publish(
                "status/imager", '{"status":"Error: Shutter speed not valid"}'
            )
            raise e

    def __message_settings_wb_gain(self, settings):
        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")
        if self._camera.controls is None:
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
            self._camera.controls.white_balance_gains = self.__white_balance_gain
        except ValueError as e:
            loguru.logger.error("the requested white balance gain is not valid!")
            self._mqtt.client.publish(
                "status/imager",
                '{"status":"Error: White balance gain not valid"}',
            )
            raise e

    def __message_settings_wb(self, settings):
        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")
        if self._camera.controls is None:
            raise RuntimeError("camera has not started yet")

        loguru.logger.debug(
            f"updating the camera white balance mode to {settings['white_balance']}"
        )
        self.__white_balance_mode = settings.get("white_balance", self.__white_balance_mode)
        loguru.logger.debug(
            f"updating the camera white balance mode to {self.__white_balance_mode}"
        )
        try:
            self._camera.controls.white_balance_mode = self.__white_balance_mode
        except ValueError as e:
            loguru.logger.error("the requested white balance is not valid!")
            self._mqtt.client.publish(
                "status/imager",
                f'{"status":"Error: Invalid white balance mode {self.__white_balance_mode}"}',
            )
            raise e

    def __message_settings_image_gain(self, settings):
        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")
        if self._camera.controls is None:
            raise RuntimeError("camera has not started yet")

        if "analog" in settings["image_gain"]:
            loguru.logger.debug(
                f"updating the camera image analog gain to {settings['image_gain']}"
            )
            self.__image_gain = settings["image_gain"].get("analog", self.__image_gain)
        loguru.logger.debug(f"updating the camera image gain to {self.__image_gain}")
        try:
            self._camera.controls.image_gain = self.__image_gain
        except ValueError as e:
            loguru.logger.error("the requested image gain is not valid!")
            self._mqtt.client.publish(
                "status/imager",
                '{"status":"Error: Image gain not valid"}',
            )
            raise e

    # copied #
    @loguru.logger.catch
    def handle_new_message(self) -> None:
        """Handle a new message received over MQTT."""
        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")

        loguru.logger.info("we received a new message")
        if not self._mqtt.msg["topic"].startswith("imager/"):
            loguru.logger.error(
                f"the received message was not for us! topic was {self._mqtt.msg['topic']}"
            )
            self._mqtt.read_message()
            return

        if self._mqtt is None:
            raise RuntimeError("imager mqtt client is not running yet")

        latest_message = self._mqtt.msg["payload"]
        loguru.logger.debug(latest_message)
        action = self._mqtt.msg["payload"]["action"]
        loguru.logger.debug(action)

        if action == "update_config":
            self.__message_update(latest_message)
            self._mqtt.read_message()
            return
        if action == "settings":
            self.__message_settings(latest_message)
            self._mqtt.read_message()
            return
        if action == "stop":
            if self._routine is not None:
                self._routine.stop()
                self._routine = None
            self._mqtt.read_message()
            return
        if action == "image":
            # {"action":"image","sleep":5,"volume":1,"nb_frame":200}
            self._start_acquisition(latest_message)
            self._mqtt.read_message()
            return
        if action == "":  # FIXME(ethanjli): is this case really needed?
            self._mqtt.read_message()
            return
        loguru.logger.warning(
            f"We did not understand the received request ({action}): {latest_message}"
        )
        self._mqtt.read_message()

    # TODO replicate the remaining methods of the initial imager

    ################################################################################
    # While loop for capturing commands from Node-RED
    ################################################################################
    @loguru.logger.catch
    def run(self):
        """This is the function that needs to be started to create a thread"""
        loguru.logger.info(f"The imager control thread has been started in process {os.getpid()}")
        # MQTT Service connection
        self._mqtt = mqtt.MQTT_Client(topic="imager/#", name="imager_client")
        self._pump = MQTTPump()
        self._pump.open()

        self._mqtt.client.publish("status/imager", '{"status":"Starting up"}')

        loguru.logger.info("Starting the camera and streaming server threads")
        try:
            # Note(ethanjli): the camera must be configured and started in the same process as
            # anything which uses self.preview_stream, such as our StreamingHandler. This is because
            # self.preview_stream does not synchronize state across independent processes! We also
            # need the MQTT client to live in the same process as the camera, because the MQTT
            # client directly accesses the camera controls (this keeps the code simpler than passing
            # all camera settings queries/changes through a multiprocessing.Queue).
            self._camera.open()
            self._announce_camera_name()

        except Exception as e:
            loguru.logger.exception(f"An exception has occured when starting up picamera2: {e}")
            try:
                self._camera.close()
                self._camera.open()
                self._announce_camera_name()
            except Exception as e_second:
                loguru.logger.exception(
                    f"A second exception has occured when starting up picamera2: {e_second}"
                )
                loguru.logger.error("This error can't be recovered from, terminating now")
                raise e_second

        if self._camera.controls is None:
            raise RuntimeError("Camera was unable to start")

        loguru.logger.info("Initialising the camera with the default settings...")
        # TODO identify the camera parameters that can be accessed and initialize them
        time.sleep(0.1)  # FIXME: block on the camera until the controls are ready?
        self._camera.controls.exposure_time = self.__exposure_time
        time.sleep(0.1)
        self._camera.controls.exposure_mode = self.__exposure_mode
        time.sleep(0.1)
        self._camera.controls.white_balance_mode = self.__white_balance_mode
        time.sleep(0.1)
        self._camera.controls.white_balance_gains = self.__white_balance_gain
        time.sleep(0.1)
        self._camera.controls.image_gain = self.__image_gain

        # if self._camera.sensor_name == "IMX219":  # Camera v2.1
        #     self.__resolution = (3280, 2464)
        # elif self._camera.sensor_name == "IMX477":  # Camera HQ
        #     self.__resolution = (4056, 3040)
        # else:
        #     self.__resolution = (1280, 1024)
        #     loguru.logger.error(
        #         f"The connected camera {self._camera.sensor_name} is not recognized, "
        #         + "please check your camera"
        #     )

        try:
            address = ("", 8000)
            server = mjpeg.StreamingServer(self.preview_stream, address)
            # FIXME(ethanjli): make this not be a daemon thread, by recovering resourcse
            # appropriately at shutdown!
            self._streaming_thread = threading.Thread(target=server.serve_forever, daemon=True)
            self._streaming_thread.start()

            # Publish the status "Ready" to Node-RED via MQTT
            self._mqtt.client.publish("status/imager", '{"status":"Ready"}')

            loguru.logger.success("Camera is READY!")

            # Move to the state of getting ready to start instead of stop by default
            # self.__imager.change(planktoscope.imagernew.state_machine.Imaging)

            # While loop for capturing commands from Node-RED (later! the display is prior)
            while not self.stop_event.is_set():
                if self._routine is not None and not self._routine.is_alive():
                    # Garbage-collect any finished image-acquisition routine threads so that we're
                    # ready for the next configuration update command which arrives:
                    self._routine.stop()
                    self._routine = None
                if not self._mqtt.new_message_received():
                    time.sleep(0.1)
                    continue

                self.handle_new_message()

        finally:
            loguru.logger.info("Shutting down the imager process")
            self._mqtt.client.publish("status/imager", '{"status":"Dead"}')
            # NOTE the resource release task of the camera is handled within the thread
            # loguru.logger.debug("Stopping picamera and its thread")
            # self.shutdown_event.set()
            # self._camera.stop()
            # self._camera.close()
            self._pump.close()
            loguru.logger.debug("Stopping the streaming thread")
            server.shutdown()
            loguru.logger.debug("Stopping MQTT")
            self._mqtt.shutdown()
            loguru.logger.success("Imager process shut down! See you!")

    def _announce_camera_name(self) -> None:
        """Announce the camera's sensor name as a status update."""
        if self._mqtt is None:
            raise RuntimeError("Imager MQTT client is not running yet")
        if self._camera.controls is None:
            raise RuntimeError("Camera has not started yet")

        camera_names = {
            "IMX219": "Camera v2.1",
            "IMX477": "Camera HQ",
        }
        self._mqtt.client.publish(
            "status/imager",
            json.dumps(
                {
                    "camera_name": camera_names.get(
                        self._camera.controls.sensor_name, "Not recognized"
                    ),
                }
            ),
        )


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

    # FIXME(ethanjli): implement a stop method
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
            if not self._discrete_run.locked():
                continue
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


# FIXME(ethanjli): split off the camera MQTT API into a separate class
