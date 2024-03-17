import datetime
import json
import multiprocessing
import os
import threading
import time

import loguru

from planktoscope import identity, integrity, mqtt
from planktoscope.imagernew import camera, mjpeg, state_machine, streams

loguru.logger.info("planktoscope.imager is loaded")


class ImagerProcess(multiprocessing.Process):
    """This class contains the main definitions for the imager of the PlanktoScope"""

    # def __init__(self, stop_event, exposure_time=10000, iso=100):
    def __init__(self, stop_event, exposure_time=10000):
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
        self.__imager = state_machine.Imager()
        self.__img_goal = 0
        self.__img_done = 0
        self.__sleep_before = None
        self.__pump_volume = None
        self.__pump_direction = "FORWARD"
        self.__img_goal = None
        self.imager_client = None
        self.__error = 0

        # Initialize the camera
        self.preview_stream = streams.LatestByteBuffer()
        self.__camera = camera.PiCamera(self.preview_stream)
        # self.__resolution = None  # this is set by the start method

        # self.__iso = iso
        self.__exposure_time = exposure_time
        self.__exposure_mode = "normal"  # "auto"
        self.__white_balance = "off"
        self.__white_balance_gain = (
            configuration.get("red_gain", 2.00),
            configuration.get("blue_gain", 1.40),
        )
        self.__image_gain = configuration.get("analog_gain", 1.00)

        self.__base_path = "/home/pi/data/img"
        # Let's make sure the base path exists
        if not os.path.exists(self.__base_path):
            os.makedirs(self.__base_path)

        self.__export_path = ""
        self.__global_metadata = None

        loguru.logger.success("planktoscope.imager is initialised and ready to go!")

    # copied #
    def __message_image(self, last_message):
        """Actions for when we receive a message"""
        if (
            "sleep" not in last_message
            or "volume" not in last_message
            or "nb_frame" not in last_message
            or "pump_direction" not in last_message
        ):
            loguru.logger.error(f"The received message has the wrong argument {last_message}")
            self.imager_client.client.publish("status/imager", '{"status":"Error"}')
            return
        self.__imager.change(state_machine.Imaging)

        # Get duration to wait before an image from the different received arguments
        self.__sleep_before = float(last_message["sleep"])

        # Get volume in between two images from the different received arguments
        self.__pump_volume = float(last_message["volume"])

        # Get the pump direction message
        self.__pump_direction = last_message["pump_direction"]

        # Get the number of frames to image from the different received arguments
        self.__img_goal = int(last_message["nb_frame"])

        # Reset the counter to 0
        self.__img_done = 0

        self.imager_client.client.publish("status/imager", '{"status":"Started"}')

    # copied #
    def __message_stop(self):
        self.imager_client.client.unsubscribe("status/pump")

        # Stops the pump
        self.imager_client.client.publish("actuator/pump", '{"action": "stop"}')

        loguru.logger.info("The imaging has been interrupted.")

        # Publish the status "Interrupted" to Node-RED via MQTT
        self.imager_client.client.publish("status/imager", '{"status":"Interrupted"}')

        self.__imager.change(state_machine.Stop)

    # copied #
    def __message_update(self, last_message):
        if self.__imager.state.name == "stop":
            if "config" not in last_message:
                loguru.logger.error(f"The received message has the wrong argument {last_message}")
                self.imager_client.client.publish(
                    "status/imager", '{"status":"Configuration message error"}'
                )
                return

            loguru.logger.info("Updating the configuration now with the received data")
            # Updating the configuration with the passed parameter in payload["config"]
            self.__global_metadata = last_message["config"]

            # Publish the status "Config updated" to Node-RED via MQTT
            self.imager_client.client.publish("status/imager", '{"status":"Config updated"}')
            loguru.logger.info("Configuration has been updated")
        else:
            loguru.logger.error("We can't update the configuration while we are imaging.")
            # Publish the status "Interrupted" to Node-RED via MQTT
            self.imager_client.client.publish("status/imager", '{"status":"Busy"}')

    def __message_settings(self, last_message):
        if self.__imager.state.name != "stop":
            loguru.logger.error("We can't update the camera settings while we are imaging.")
            # Publish the status "Interrupted" to via MQTT to Node-RED
            self.imager_client.client.publish("status/imager", '{"status":"Busy"}')
            return

        if "settings" not in last_message:
            loguru.logger.error(f"The received message has the wrong argument {last_message}")
            self.imager_client.client.publish("status/imager", '{"status":"Camera settings error"}')
            return

        loguru.logger.info("Updating the camera settings now with the received data")
        # Updating the configuration with the passed parameter in payload["config"]
        settings = last_message["settings"]

        try:
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

        # Publish the status "Config updated" to via MQTT to Node-RED
        self.imager_client.client.publish("status/imager", '{"status":"Camera settings updated"}')
        loguru.logger.info("Camera settings have been updated")

    def __message_settings_ss(self, settings):
        self.__exposure_time = settings.get("shutter_speed", self.__exposure_time)
        loguru.logger.debug(f"Updating the camera shutter speed to {self.__exposure_time}")
        try:
            self.__camera.exposure_time = self.__exposure_time
        except TimeoutError:
            loguru.logger.error(
                "A timeout has occured when setting the shutter speed, trying again"
            )
            self.__camera.exposure_time = self.__exposure_time
        except ValueError as e:
            loguru.logger.error("The requested shutter speed is not valid!")
            self.imager_client.client.publish(
                "status/imager", '{"status":"Error: Shutter speed not valid"}'
            )
            raise e

    def __message_settings_wb_gain(self, settings):
        if "red" in settings["white_balance_gain"]:
            loguru.logger.debug(
                "Updating the camera white balance red gain to "
                + f"{settings['white_balance_gain']}"
            )
            self.__white_balance_gain = (
                settings["white_balance_gain"].get("red", self.__white_balance_gain[0]),
                self.__white_balance_gain[1],
            )
        if "blue" in settings["white_balance_gain"]:
            loguru.logger.debug(
                "Updating the camera white balance blue gain to "
                + f"{settings['white_balance_gain']}"
            )
            self.__white_balance_gain = (
                self.__white_balance_gain[0],
                settings["white_balance_gain"].get("blue", self.__white_balance_gain[1]),
            )
        loguru.logger.debug(
            f"Updating the camera white balance gain to {self.__white_balance_gain}"
        )
        try:
            self.__camera.white_balance_gain = self.__white_balance_gain
        except TimeoutError:
            loguru.logger.error(
                "A timeout has occured when setting the white balance gain, trying again"
            )
            self.__camera.white_balance_gain = self.__white_balance_gain
        except ValueError as e:
            loguru.logger.error("The requested white balance gain is not valid!")
            self.imager_client.client.publish(
                "status/imager",
                '{"status":"Error: White balance gain not valid"}',
            )
            raise e

    def __message_settings_wb(self, settings):
        loguru.logger.debug(
            f"Updating the camera white balance mode to {settings['white_balance']}"
        )
        self.__white_balance = settings.get("white_balance", self.__white_balance)
        loguru.logger.debug(f"Updating the camera white balance mode to {self.__white_balance}")
        try:
            self.__camera.white_balance = self.__white_balance
        except TimeoutError:
            loguru.logger.error(
                "A timeout has occured when setting the white balance, trying again"
            )
            self.__camera.white_balance = self.__white_balance
        except ValueError as e:
            loguru.logger.error("The requested white balance is not valid!")
            self.imager_client.client.publish(
                "status/imager",
                f'{"status":"Error: Invalid white balance mode {self.__white_balance}"}',
            )
            raise e

    def __message_settings_image_gain(self, settings):
        if "analog" in settings["image_gain"]:
            loguru.logger.debug(
                f"Updating the camera image analog gain to {settings['image_gain']}"
            )
            self.__image_gain = settings["image_gain"].get("analog", self.__image_gain)
        loguru.logger.debug(f"Updating the camera image gain to {self.__image_gain}")
        try:
            self.__camera.image_gain = self.__image_gain
        except TimeoutError:
            loguru.logger.error(
                "A timeout has occured when setting the white balance gain, trying again"
            )
            self.__camera.image_gain = self.__image_gain
        except ValueError as e:
            loguru.logger.error("The requested image gain is not valid!")
            self.imager_client.client.publish(
                "status/imager",
                '{"status":"Error: Image gain not valid"}',
            )
            raise e

    # copied #
    @loguru.logger.catch
    def treat_message(self):
        action = ""
        loguru.logger.info("We received a new message")
        if self.imager_client.msg["topic"].startswith("imager/"):
            last_message = self.imager_client.msg["payload"]
            loguru.logger.debug(last_message)
            action = self.imager_client.msg["payload"]["action"]
            loguru.logger.debug(action)
        elif self.imager_client.msg["topic"] == "status/pump":
            loguru.logger.debug(f"Status message payload is {self.imager_client.msg['payload']}")
            if self.__imager.state.name == "waiting":
                if self.imager_client.msg["payload"]["status"] == "Done":
                    self.__imager.change(state_machine.Capture)
                    self.imager_client.client.unsubscribe("status/pump")
                else:
                    loguru.logger.info(
                        f"The pump is not done yet {self.imager_client.msg['payload']}"
                    )
            else:
                loguru.logger.error("There is an error, we received an unexpected pump message")
        else:
            loguru.logger.error(
                f"The received message was not for us! Topic was {self.imager_client.msg['topic']}"
            )
        self.imager_client.read_message()

        # If the command is "image"
        if action == "image":
            # {"action":"image","sleep":5,"volume":1,"nb_frame":200}
            self.__message_image(last_message)

        elif action == "stop":
            self.__message_stop()

        elif action == "update_config":
            self.__message_update(last_message)

        elif action == "settings":
            self.__message_settings(last_message)

        elif action not in ["image", "stop", "update_config", "settings", ""]:
            loguru.logger.warning(
                f"We did not understand the received request {action} - {last_message}"
            )

    # copied #
    def __pump_message(self):
        """Sends a message to the pump process"""

        # Pump during a given volume
        self.imager_client.client.publish(
            "actuator/pump",
            json.dumps(
                {
                    "action": "move",
                    "direction": self.__pump_direction,
                    "volume": self.__pump_volume,
                    "flowrate": 2,
                }
            ),
        )

    def __state_imaging(self):
        # subscribe to status/pump
        self.imager_client.client.subscribe("status/pump")

        # Definition of the few important metadata
        local_metadata = {
            "acq_local_datetime": datetime.datetime.now().isoformat().split(".")[0],
            "acq_camera_shutter_speed": self.__exposure_time,
            "acq_uuid": identity.load_machine_name(),
            "sample_uuid": identity.load_machine_name(),
        }

        # Concat the local metadata and the metadata from Node-RED
        self.__global_metadata = {**self.__global_metadata, **local_metadata}

        if "object_date" not in self.__global_metadata:
            # If this path exists, then ids are reused when they should not
            loguru.logger.error("The metadata did not contain object_date!")
            self.imager_client.client.publish(
                "status/imager",
                '{"status":"Configuration update error: object_date is missing!"}',
            )
            # Reset the counter to 0
            self.__img_done = 0
            # Change state towards stop
            self.__imager.change(state_machine.Stop)
            return

        loguru.logger.info("Setting up the directory structure for storing the pictures")
        self.__export_path = os.path.join(
            self.__base_path,
            self.__global_metadata["object_date"],
            str(self.__global_metadata["sample_id"]).replace(" ", "_").strip("'"),
            str(self.__global_metadata["acq_id"]).replace(" ", "_").strip("'"),
        )

        if os.path.exists(self.__export_path):
            # If this path exists, then ids are reused when they should not
            loguru.logger.error(f"The export path at {self.__export_path} already exists")
            self.imager_client.client.publish(
                "status/imager",
                '{"status":"Configuration update error: Chosen id are already in use!"}',
            )
            # Reset the counter to 0
            self.__img_done = 0
            self.__imager.change(state_machine.Stop)

        # create the path!
        os.makedirs(self.__export_path)

        # Export the metadata to a json file
        loguru.logger.info("Exporting the metadata to a metadata.json")
        metadata_filepath = os.path.join(self.__export_path, "metadata.json")
        with open(metadata_filepath, "w", encoding="utf-8") as metadata_file:
            json.dump(self.__global_metadata, metadata_file, indent=4)
            loguru.logger.debug(f"Metadata dumped in {metadata_file} are {self.__global_metadata}")

        # Create the integrity file in this export path
        try:
            integrity.create_integrity_file(self.__export_path)
        except FileExistsError:
            loguru.logger.info(
                f"The integrity file already exists in this export path {self.__export_path}"
            )
        # Add the metadata.json file to the integrity file
        try:
            integrity.append_to_integrity_file(metadata_filepath)
        except FileNotFoundError:
            loguru.logger.error(
                f"{metadata_filepath} was not found, the metadata file may not have been created!"
            )

        self.__pump_message()

        self.__imager.change(state_machine.Waiting)

    def __state_capture(self):
        filename = f"{datetime.datetime.now().strftime('%H_%M_%S_%f')}.jpg"

        # Define the filename of the image
        filename_path = os.path.join(self.__export_path, filename)

        loguru.logger.info(
            f"Capturing image {self.__img_done + 1}/{self.__img_goal} to {filename_path}"
        )

        # Sleep a duration before to start acquisition
        time.sleep(self.__sleep_before)

        # Capture an image to the temporary file
        try:
            self.__camera.capture_file(filename_path)
        except TimeoutError:
            self.__capture_error("timeout during capture")
            return

        loguru.logger.debug("Syncing the disk")
        os.sync()

        # Add the checksum of the captured image to the integrity file
        try:
            integrity.append_to_integrity_file(filename_path)
        except FileNotFoundError:
            self.__capture_error(f"{filename_path} was not found")
            return

        self.imager_client.client.publish(
            "status/imager",
            f'{{"status":"Image {self.__img_done + 1}/{self.__img_goal} saved to {filename}"}}',
        )

        # Increment the counter
        self.__img_done += 1
        self.__error = 0

        # If counter reach the number of frame, break
        if self.__img_done >= self.__img_goal:
            self.__img_done = 0

            self.imager_client.client.publish("status/imager", '{"status":"Done"}')

            self.__imager.change(state_machine.Stop)
        else:
            # We have not reached the final stage, let's keep imaging
            self.imager_client.client.subscribe("status/pump")

            self.__pump_message()

            self.__imager.change(state_machine.Waiting)

    # copied #
    def __capture_error(self, message=""):
        loguru.logger.error(f"An error occurred during the capture: {message}")
        if self.__error:
            loguru.logger.error("This is a repeating problem, stopping the capture now")
            self.imager_client.client.publish(
                "status/imager",
                f'{{"status":"Image {self.__img_done + 1}/{self.__img_goal} WAS NOT CAPTURED! '
                + 'STOPPING THE PROCESS!"}}',
            )
            self.__img_done = 0
            self.__img_goal = 0
            self.__error = 0
            self.__imager.change(state_machine.Stop)
        else:
            self.__error += 1
            self.imager_client.client.publish(
                "status/imager",
                f'{{"status":"Image {self.__img_done + 1}/{self.__img_goal} was not captured '
                + 'due to this error:{message}! Retrying once!"}}',
            )
        time.sleep(1)

    # copied #
    @loguru.logger.catch
    def state_machine(self):
        if self.__imager.state.name == "imaging":
            self.__state_imaging()
            return

        if self.__imager.state.name == "capture":
            self.__state_capture()
            return

    # TODO replicate the remaining methods of the initial imager

    ################################################################################
    # While loop for capturing commands from Node-RED
    ################################################################################
    @loguru.logger.catch
    def run(self):
        """This is the function that needs to be started to create a thread"""
        loguru.logger.info(f"The imager control thread has been started in process {os.getpid()}")
        # MQTT Service connection
        self.imager_client = mqtt.MQTT_Client(topic="imager/#", name="imager_client")

        self.imager_client.client.publish("status/imager", '{"status":"Starting up"}')

        loguru.logger.info("Starting the camera and streaming server threads")
        try:
            # Note(ethanjli): the camera must be configured and started in the same process as
            # anything which uses self.preview_stream, such as our StreamingHandler. This is because
            # self.preview_stream does not synchronize state across independent processes! We also
            # need the MQTT client to live in the same process as the camera, because the MQTT
            # client directly accesses the camera controls (this keeps the code simpler than passing
            # all camera settings queries/changes through a multiprocessing.Queue).
            self.__camera.open()
            self._announce_camera_name()

        except Exception as e:
            loguru.logger.exception(f"An exception has occured when starting up picamera2: {e}")
            try:
                self.__camera.close()
                self.__camera.open()
                self._announce_camera_name()
            except Exception as e_second:
                loguru.logger.exception(
                    f"A second exception has occured when starting up picamera2: {e_second}"
                )
                loguru.logger.error("This error can't be recovered from, terminating now")
                raise e_second

        loguru.logger.info("Initialising the camera with the default settings...")
        # TODO identify the camera parameters that can be accessed and initialize them
        self.__camera.exposure_time = self.__exposure_time
        time.sleep(0.1)
        self.__camera.exposure_mode = self.__exposure_mode
        time.sleep(0.1)
        self.__camera.white_balance = self.__white_balance
        time.sleep(0.1)
        self.__camera.white_balance_gain = self.__white_balance_gain
        time.sleep(0.1)
        self.__camera.image_gain = self.__image_gain

        # if self.__camera.sensor_name == "IMX219":  # Camera v2.1
        #     self.__resolution = (3280, 2464)
        # elif self.__camera.sensor_name == "IMX477":  # Camera HQ
        #     self.__resolution = (4056, 3040)
        # else:
        #     self.__resolution = (1280, 1024)
        #     loguru.logger.error(
        #         f"The connected camera {self.__camera.sensor_name} is not recognized, "
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
            self.imager_client.client.publish("status/imager", '{"status":"Ready"}')

            loguru.logger.success("Camera is READY!")

            # Move to the state of getting ready to start instead of stop by default
            # self.__imager.change(planktoscope.imagernew.state_machine.Imaging)

            # While loop for capturing commands from Node-RED (later! the display is prior)
            while not self.stop_event.is_set():
                if self.imager_client.new_message_received():
                    self.treat_message()
                self.state_machine()
                # Do nothing instead of message reception and treatment
                # pass
                time.sleep(0.1)

        finally:
            loguru.logger.info("Shutting down the imager process")
            self.imager_client.client.publish("status/imager", '{"status":"Dead"}')
            # NOTE the resource release task of the camera is handled within the thread
            # loguru.logger.debug("Stopping picamera and its thread")
            # self.shutdown_event.set()
            # self.__camera.stop()
            # self.__camera.close()
            loguru.logger.debug("Stopping the streaming thread")
            server.shutdown()
            loguru.logger.debug("Stopping MQTT")
            self.imager_client.shutdown()
            loguru.logger.success("Imager process shut down! See you!")

    def _announce_camera_name(self) -> None:
        """Announce the camera's sensor name as a status update."""
        camera_names = {
            "IMX219": "Camera v2.1",
            "IMX477": "Camera HQ",
        }
        self.imager_client.client.publish(
            "status/imager",
            json.dumps(
                {
                    "camera_name": camera_names.get(
                        self.__camera.controls.sensor_name, "Not recognized"
                    ),
                }
            ),
        )
