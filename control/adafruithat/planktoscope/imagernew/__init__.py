import datetime # needed to get date and time for folder name and filename
import time # needed to able to sleep for a given duration
import json
import os
import shutil
import multiprocessing
import threading # needed for the streaming server
import functools # needed for the streaming server

from loguru import logger

import planktoscope.mqtt
import planktoscope.imagernew.state_machine
import planktoscope.imagernew.picamera
import planktoscope.imagernew.picam_streamer
import planktoscope.integrity
import planktoscope.identity


logger.info("planktoscope.imager is loaded")

################################################################################
# Main Imager class
################################################################################
class ImagerProcess(multiprocessing.Process):
    """This class contains the main definitions for the imager of the PlanktoScope"""

    def __init__(self, stop_event, iso=100, shutter_speed=1):
        """Initialize the Imager class

        Args:
            stop_event (multiprocessing.Event): shutdown event
            iso (int, optional): ISO sensitivity. Defaults to 100.
            shutter_speed (int, optional): Shutter speed of the camera. Defaults to 1.
        """
        super(ImagerProcess, self).__init__(name="imager")

        logger.info("planktoscope.imager is initialising")

        if os.path.exists("/home/pi/PlanktoScope/hardware.json"):
            # load hardware.json
            with open("/home/pi/PlanktoScope/hardware.json", "r") as config_file:
                configuration = json.load(config_file)
                logger.debug(f"Hardware configuration loaded is {configuration}")
        else:
            logger.info(
                "The hardware configuration file doesn't exists, using defaults"
            )
            configuration = {}

        self.__camera_type = "v2.1"

        # parse the config data. If the key is absent, we are using the default value
        self.__camera_type = configuration.get("camera_type", self.__camera_type)

        self.stop_event = stop_event
        self.__imager = planktoscope.imagernew.state_machine.Imager()
        self.__img_goal = 0
        self.__img_done = 0
        self.__sleep_before = None
        self.__pump_volume = None
        self.__pump_direction = "FORWARD"
        self.__img_goal = None
        self.imager_client = None
        self.streaming_output = planktoscope.imagernew.picam_streamer.StreamingOutput()
        self.__error = 0

        # Initialize the camera
        self.__camera = planktoscope.imagernew.picamera.picamera(self.streaming_output)

        # Start the streaming
        try:
            self.__camera.start()
        except Exception as e:
            logger.exception(
                f"An exception has occured when starting up picamera2: {e}"
            )
            try:
                self.__camera.start(True)
            except Exception as e:
                logger.exception(
                    f"A second exception has occured when starting up picamera2: {e}"
                )
                logger.error("This error can't be recovered from, terminating now")
                raise e

        """if self.__camera.sensor_name == "IMX219":  # Camera v2.1
            self.__resolution = (3280, 2464)
        elif self.__camera.sensor_name == "IMX477":  # Camera HQ
            self.__resolution = (4056, 3040)
        else:
            self.__resolution = (1280, 1024)
            logger.error(
                f"The connected camera {self.__camera.sensor_name} is not recognized, please check your camera"
            )"""

        #self.__iso = iso
        #self.__shutter_speed = shutter_speed
        self.__exposure_mode = "normal" #"auto"
        self.__white_balance = "off"
        self.__white_balance_gain = (
            configuration.get("red_gain", 2.00),
            configuration.get("blue_gain", 1.40)
        )
        self.__image_gain = configuration.get("analog_gain", 1.00)

        self.__base_path = "/home/pi/data/img"
        # Let's make sure the base path exists
        if not os.path.exists(self.__base_path):
            os.makedirs(self.__base_path)

        self.__export_path = ""
        self.__global_metadata = None

        logger.info("Initialising the camera with the default settings")
        # TODO identify the camera parameters that can be accessed and initialize them
        self.__camera.exposure_mode = self.__exposure_mode
        time.sleep(0.1)
        
        self.__camera.white_balance = self.__white_balance
        time.sleep(0.1)

        self.__camera.white_balance_gain = self.__white_balance_gain
        time.sleep(0.1)

        self.__camera.image_gain = self.__image_gain

        logger.success("planktoscope.imager is initialised and ready to go!")

    # copied #
    def __message_image(self, last_message):
        """Actions for when we receive a message"""
        if (
            "sleep" not in last_message
            or "volume" not in last_message
            or "nb_frame" not in last_message
            or "pump_direction" not in last_message
        ):
            logger.error(f"The received message has the wrong argument {last_message}")
            self.imager_client.client.publish("status/imager", '{"status":"Error"}')
            return
        self.__imager.change(planktoscope.imagernew.state_machine.Imaging)

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

        logger.info("The imaging has been interrupted.")

        # Publish the status "Interrupted" to via MQTT to Node-RED
        self.imager_client.client.publish("status/imager", '{"status":"Interrupted"}')

        self.__imager.change(planktoscope.imagernew.state_machine.Stop)

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

    # TODO replicate the remaining methods of the initial imager

    #def __message_update(self, last_message):

    #def __message_settings(self, last_message):

    #@logger.catch
    #def treat_message(self):

    #def __state_imaging(self):

    #def __state_capture(self):

    #def __capture_error(self, message=""):

    #@logger.catch
    #def state_machine(self):

    ################################################################################
    # While loop for capturing commands from Node-RED
    ################################################################################
    @logger.catch
    def run(self):
        """This is the function that needs to be started to create a thread"""
        logger.info(
            f"The imager control thread has been started in process {os.getpid()}"
        )
        # MQTT Service connection
        self.imager_client = planktoscope.mqtt.MQTT_Client(
            topic="imager/#", name="imager_client"
        )

        self.imager_client.client.publish("status/imager", '{"status":"Starting up"}')

        if self.__camera.sensor_name == "IMX219":  # Camera v2.1
            self.imager_client.client.publish(
                "status/imager", '{"camera_name":"Camera v2.1"}'
            )
        elif self.__camera.sensor_name == "IMX477":  # Camera HQ
            self.imager_client.client.publish(
                "status/imager", '{"camera_name":"HQ Camera"}'
            )
        else:
            self.imager_client.client.publish(
                "status/imager", '{"camera_name":"Not recognized"}'
            )

        logger.info("Starting the streaming server thread")
        try:
            address = ("", 8000)
            fps = 15
            refresh_delay = 1 / fps
            handler = functools.partial(
                planktoscope.imagernew.stream.StreamingHandler, refresh_delay, self.streaming_output
            )
            server = planktoscope.imagernew.stream.StreamingServer(address, handler)
            self.streaming_thread = threading.Thread(
                target=server.serve_forever, daemon=True
            )
            self.streaming_thread.start()

            # Publish the status "Ready" to Node-RED via MQTT
            self.imager_client.client.publish("status/imager", '{"status":"Ready"}')

            logger.success("Camera is READY!")

            ################### Move to the state of getting ready to start instead of stop by default
            #self.__imager.change(planktoscope.imagernew.state_machine.Imaging)

            ################### While loop for capturing commands from Node-RED (later! the display is prior)
            while not self.stop_event.is_set():
                """if self.imager_client.new_message_received():
                    self.treat_message()
                self.state_machine()"""
                # Do nothing instead of message reception and treatment
                pass
                time.sleep(0.1)
                
        finally:
            logger.info("Shutting down the imager process")
            self.imager_client.client.publish("status/imager", '{"status":"Dead"}')
            logger.debug("Stopping picamera")
            self.__camera.stop()
            self.__camera.close()
            logger.debug("Stopping the streaming thread")
            server.shutdown()
            logger.debug("Stopping MQTT")
            self.imager_client.shutdown()
            logger.success("Imager process shut down! See you!")
