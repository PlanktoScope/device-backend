"""
This module provides the functionality to control the pump mechanism
of the Planktoscope.
"""

# Libraries to control the steppers for pumping
import json
import multiprocessing
import os
import time
import typing

from loguru import logger

import shush
from planktoscope import mqtt

logger.info("planktoscope.stepper is loaded")

FORWARD = 1
BACKWARD = 2
STEPPER1 = 0
STEPPER2 = 1


class Stepper:
    """
    This class controls the stepper motor used for adjusting the pump.
    """

    def __init__(self, stepper):
        """Initialize the stepper class

        Args:
            stepper (either STEPPER1 or STEPPER2): reference to the object that controls the stepper
            size (int): maximum number of steps of this stepper (aka stage size). Can be 0 if not
              applicable
        """
        self.__stepper = shush.Motor(stepper).disable_motor()
        self.__goal = 0
        self.__direction: typing.Optional[int] = None

    def at_goal(self):
        """Is the motor at its goal

        Returns:
            Bool: True if position and goal are identical
        """
        return self.__stepper.get_position() == self.__goal

    def is_moving(self):
        """is the stepper in movement?

        Returns:
          Bool: True if the stepper is moving
        """
        return self.__stepper.get_velocity() != 0

    def go(self, direction, distance):
        """
        Move in the given direction for the given distance.

        Args:
            direction (int): The movement direction (FORWARD or BACKWARD).
            distance (int): The distance to move.
        """
        self.__direction = direction
        if self.__direction == FORWARD:
            self.__goal = int(self.__stepper.get_position() + distance)
        elif self.__direction == BACKWARD:
            self.__goal = int(self.__stepper.get_position() - distance)
        else:
            logger.error(f"The given direction is wrong {direction}")
        self.__stepper.enable_motor()
        self.__stepper.go_to(self.__goal)

    def shutdown(self):
        """
        Shutdown everything ASAP.
        """
        self.__stepper.stop_motor()
        self.__stepper.disable_motor()
        self.__goal = self.__stepper.get_position()

    def release(self):
        self.__stepper.disable_motor()

    @property
    def speed(self):
        return self.__stepper.ramp_VMAX

    @speed.setter
    def speed(self, speed):
        """Change the stepper speed

        Args:
            speed (int): speed of the movement by the stepper, in microsteps unit/s
        """
        logger.debug(f"Setting stepper speed to {speed}")
        self.__stepper.ramp_VMAX = int(speed)

    @property
    def acceleration(self):
        return self.__stepper.ramp_AMAX

    @acceleration.setter
    def acceleration(self, acceleration):
        """Change the stepper acceleration

        Args:
            acceleration (int): acceleration reachable by the stepper, in microsteps unit/s²
        """
        logger.debug(f"Setting stepper acceleration to {acceleration}")
        self.__stepper.ramp_AMAX = int(acceleration)

    @property
    def deceleration(self):
        return self.__stepper.ramp_DMAX

    @deceleration.setter
    def deceleration(self, deceleration):
        """Change the stepper deceleration

        Args:
            deceleration (int): deceleration reachable by the stepper, in microsteps unit/s²
        """
        logger.debug(f"Setting stepper deceleration to {deceleration}")
        self.__stepper.ramp_DMAX = int(deceleration)


class PumpProcess(multiprocessing.Process):
    """
    This class manages the pumping process using a stepper motor.
    """

    # 5200 for custom NEMA14 pump with 0.8mm ID Tube
    pump_steps_per_ml = 507

    # pump max speed is in ml/min
    pump_max_speed = 50

    def __init__(self, event):
        """
        Initialize the pump process.

        Args:
            event (multiprocessing.Event): Event to control the stopping of the process
        """
        super(PumpProcess, self).__init__()
        logger.info("Initialising the stepper process")

        self.stop_event = event
        self.pump_started = False

        if os.path.exists("/home/pi/PlanktoScope/hardware.json"):
            # load hardware.json
            with open("/home/pi/PlanktoScope/hardware.json", "r", encoding="utf-8") as config_file:
                # TODO #100 insert guard for config_file empty
                configuration = json.load(config_file)
                logger.debug(f"Hardware configuration loaded is {configuration}")
        else:
            logger.info("The hardware configuration file doesn't exists, using defaults")
            configuration = {}

        reverse = False

        # parse the config data. If the key is absent, we are using the default value
        reverse = configuration.get("stepper_reverse", reverse)

        self.pump_steps_per_ml = configuration.get("pump_steps_per_ml", self.pump_steps_per_ml)
        self.pump_max_speed = configuration.get("pump_max_speed", self.pump_max_speed)

        # define the names for the 2 exsting steppers
        if reverse:
            self.pump_stepper = Stepper(STEPPER2)

        else:
            self.pump_stepper = Stepper(STEPPER1)

        # Set pump controller max speed
        self.pump_stepper.acceleration = 2000
        self.pump_stepper.deceleration = self.pump_stepper.acceleration
        self.pump_stepper.speed = self.pump_max_speed * self.pump_steps_per_ml * 256 / 60

        logger.info("Stepper initialisation is over")

    def __message_pump(self, last_message):
        """
        Handle pump commands from received messages.

        Args:
            last_message (dict): The last received message containing pump commands.
        """
        logger.debug("We have received a pumping command")
        if last_message["action"] == "stop":
            logger.debug("We have received a stop pump command")
            self.pump_stepper.shutdown()

            # Print status
            logger.info("The pump has been interrupted")

            # Publish the status "Interrupted" to via MQTT to Node-RED
            self.actuator_client.client.publish("status/pump", '{"status":"Interrupted"}')

        elif last_message["action"] == "move":
            logger.debug("We have received a move pump command")

            if (
                "direction" not in last_message
                or "volume" not in last_message
                or "flowrate" not in last_message
            ):
                logger.error(f"The received message has the wrong argument {last_message}")
                self.actuator_client.client.publish(
                    "status/pump",
                    '{"status":"Error, the message is missing an argument"}',
                )
                return
            # Get direction from the different received arguments
            direction = last_message["direction"]
            # Get delay (in between steps) from the different received arguments
            volume = float(last_message["volume"])
            # Get number of steps from the different received arguments
            flowrate = float(last_message["flowrate"])
            if flowrate == 0:
                logger.error("The flowrate should not be == 0")
                self.actuator_client.client.publish(
                    "status/pump", '{"status":"Error, The flowrate should not be == 0"}'
                )
                return

            # Print status
            logger.info("The pump is started.")
            self.pump(direction, volume, flowrate)
        else:
            logger.warning(f"The received message was not understood {last_message}")

    def treat_command(self):
        """
        Treat the received command.
        """
        command = ""
        logger.info("We received a new message")
        last_message = self.actuator_client.msg["payload"]  # type: ignore
        logger.debug(last_message)
        command = self.actuator_client.msg["topic"].split("/", 1)[1]  # type: ignore
        logger.debug(command)
        self.actuator_client.read_message()

        if command == "pump":
            self.__message_pump(last_message)
        elif command != "":
            logger.warning(f"We did not understand the received request {command} - {last_message}")

    def pump(self, direction, volume, speed=pump_max_speed):
        """Moves the pump stepper

        Args:
            direction (string): direction of the pumping
            volume (int): volume to pump, in mL
            speed (int, optional): speed of pumping, in mL/min. Defaults to pump_max_speed.
        """

        logger.info(f"The pump will move {direction} for {volume}mL at {speed}mL/min")

        # Validation of inputs
        if direction not in ["FORWARD", "BACKWARD"]:
            logger.error("The direction command is not recognised")
            logger.error("It should be either FORWARD or BACKWARD")
            return

        # TMC5160 is configured for 256 microsteps
        nb_steps = round(self.pump_steps_per_ml * volume * 256, 0)
        logger.debug(f"The number of microsteps that will be applied is {nb_steps}")
        if speed > self.pump_max_speed:
            speed = self.pump_max_speed
            logger.warning(f"Pump speed has been clamped to a maximum safe speed of {speed}mL/min")
        steps_per_second = speed * self.pump_steps_per_ml * 256 / 60
        logger.debug(f"There will be a speed of {steps_per_second} steps per second")
        self.pump_stepper.speed = int(steps_per_second)

        # Publish the status "Started" to via MQTT to Node-RED
        self.actuator_client.client.publish(
            "status/pump",
            f'{{"status":"Started", "duration":{nb_steps / steps_per_second}}}',
        )

        # Depending on direction, select the right direction for the pump
        if direction == "FORWARD":
            self.pump_started = True
            self.pump_stepper.go(FORWARD, nb_steps)
            return

        if direction == "BACKWARD":
            self.pump_started = True
            self.pump_stepper.go(BACKWARD, nb_steps)
            return

    @logger.catch
    def run(self):
        """This is the function that needs to be started to create a thread"""
        logger.info(f"The stepper control process has been started in process {os.getpid()}")

        # Creates the MQTT Client
        # We have to create it here, otherwise when the process running run is started
        # it doesn't see changes and calls made by self.actuator_client because this one
        # only exist in the master process. See
        # https://stackoverflow.com/questions/17172878/using-pythons-multiprocessing-process-class
        self.actuator_client = mqtt.MQTT_Client(topic="actuator/#", name="actuator_client")
        # Publish the status "Ready" to via MQTT to Node-RED
        self.actuator_client.client.publish("status/pump", '{"status":"Ready"}')

        logger.success("The pump is READY!")
        while not self.stop_event.is_set():
            if self.actuator_client.new_message_received():
                self.treat_command()
            if self.pump_started and self.pump_stepper.at_goal():
                logger.success("The pump movement is over!")
                self.actuator_client.client.publish(
                    "status/pump",
                    '{"status":"Done"}',
                )
                self.pump_started = False
                self.pump_stepper.release()

            time.sleep(0.01)
        logger.info("Shutting down the stepper process")
        self.actuator_client.client.publish("status/pump", '{"status":"Dead"}')
        self.pump_stepper.shutdown()

        self.actuator_client.shutdown()
        logger.success("Stepper process shut down! See you!")


# This is called if this script is launched directly
if __name__ == "__main__":
    # TODO This should be a test suite for this library
    # Starts the stepper thread for actuators
    # This needs to be in a threading or multiprocessing wrapper
    stop_event = multiprocessing.Event()
    pump_thread = PumpProcess(event=stop_event)
    pump_thread.start()
    try:
        pump_thread.join()
    except KeyboardInterrupt:
        stop_event.set()
        pump_thread.join()
