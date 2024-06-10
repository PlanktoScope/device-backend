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

import loguru

import shush
from planktoscope import mqtt

loguru.logger.info("planktoscope.stepper is loaded")

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
        self.__stepper = shush.Motor(stepper)
        self.__stepper.disable_motor()
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
            loguru.logger.error(f"The given direction is wrong {direction}")
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
        """
        Disable the stepper motor.
        """
        self.__stepper.disable_motor()

    @property
    def speed(self):
        """
        Returns:
            int: The maximum speed (ramp_VMAX) of the stepper motor.
        """
        return self.__stepper.ramp_VMAX

    @speed.setter
    def speed(self, speed):
        """Change the stepper speed

        Args:
            speed (int): speed of the movement by the stepper, in microsteps unit/s
        """
        loguru.logger.debug(f"Setting stepper speed to {speed}")
        self.__stepper.ramp_VMAX = int(speed)

    @property
    def acceleration(self):
        """
        Returns:
            int: The maximum acceleration (ramp_AMAX) of the stepper motor.
        """
        return self.__stepper.ramp_AMAX

    @acceleration.setter
    def acceleration(self, acceleration):
        """Change the stepper acceleration

        Args:
            acceleration (int): acceleration reachable by the stepper, in microsteps unit/s²
        """
        loguru.logger.debug(f"Setting stepper acceleration to {acceleration}")
        self.__stepper.ramp_AMAX = int(acceleration)

    @property
    def deceleration(self):
        """
        Returns:
            int: The maximum deceleration (ramp_DMAX) of the stepper motor.
        """
        return self.__stepper.ramp_DMAX

    @deceleration.setter
    def deceleration(self, deceleration):
        """Change the stepper deceleration

        Args:
            deceleration (int): deceleration reachable by the stepper, in microsteps unit/s²
        """
        loguru.logger.debug(f"Setting stepper deceleration to {deceleration}")
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
        super().__init__()
        loguru.logger.info("Initialising the stepper process")

        self.stop_event = event
        self.pump_started = False
        self.actuator_client = None  # Initialize actuator_client to None

        if os.path.exists("/home/pi/PlanktoScope/hardware.json"):
            # load hardware.json
            with open("/home/pi/PlanktoScope/hardware.json", "r", encoding="utf-8") as config_file:
                # TODO #100 insert guard for config_file empty
                configuration = json.load(config_file)
                loguru.logger.debug(f"Hardware configuration loaded is {configuration}")
        else:
            loguru.logger.info("The hardware configuration file doesn't exists, using defaults")
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

        loguru.logger.info("Stepper initialisation is over")

    def __message_pump(self, last_message):
        """
        Handle the pump message received from the actuator client.

        Args:
            last_message (dict): The last message received.
        """
        loguru.logger.debug("We have received a pumping command")
        action = last_message.get("action")

        if action == "stop":
            self._handle_stop_action()
        elif action == "move":
            self._handle_move_action(last_message)
        else:
            loguru.logger.warning(f"The received message was not understood {last_message}")

    def _handle_stop_action(self):
        """
        Handle the 'stop' action for the pump.
        """
        loguru.logger.debug("We have received a stop pump command")
        self.pump_stepper.shutdown()
        loguru.logger.info("The pump has been interrupted")
        if self.actuator_client:
            self.actuator_client.client.publish("status/pump", '{"status":"Interrupted"}')

    def _handle_move_action(self, last_message):
        """
        Handle the 'move' action for the pump.

        Args:
            last_message (dict): The last message received.
        """
        loguru.logger.debug("We have received a move pump command")
        if "direction" not in last_message or "volume" not in last_message or "flowrate" not in last_message:
            loguru.logger.error(f"The received message has the wrong argument {last_message}")
            if self.actuator_client:
                self.actuator_client.client.publish("status/pump", '{"status":"Error, the message is missing an argument"}')
            return

        direction = last_message["direction"]
        volume = float(last_message["volume"])
        flowrate = float(last_message["flowrate"])

        if flowrate == 0:
            loguru.logger.error("The flowrate should not be == 0")
            if self.actuator_client:
                self.actuator_client.client.publish("status/pump", '{"status":"Error, The flowrate should not be == 0"}')
            return

        loguru.logger.info("The pump is started.")
        self.pump(direction, volume, flowrate)


    def treat_command(self):
        """
        Treat the received command.
        """
        loguru.logger.info("We received a new message")
        if not self.actuator_client:
            loguru.logger.error("Actuator client is not initialized")
            return

        last_message = self.actuator_client.msg["payload"]
        loguru.logger.debug(last_message)
        command = self.actuator_client.msg["topic"].split("/", 1)[1]
        loguru.logger.debug(command)
        self.actuator_client.read_message()

        if command == "pump":
            self.__message_pump(last_message)
        elif command != "":
            loguru.logger.warning(
                f"We did not understand the received request {command} - {last_message}")

    def pump(self, direction, volume, speed=pump_max_speed):
        """Moves the pump stepper

        Args:
            direction (string): direction of the pumping
            volume (int): volume to pump, in mL
            speed (int, optional): speed of pumping, in mL/min. Defaults to pump_max_speed.
        """

        loguru.logger.info(f"The pump will move {direction} for {volume}mL at {speed}mL/min")

        if direction not in ["FORWARD", "BACKWARD"]:
            loguru.logger.error("The direction command is not recognised")
            loguru.logger.error("It should be either FORWARD or BACKWARD")
            return

        nb_steps = round(self.pump_steps_per_ml * volume * 256, 0)
        loguru.logger.debug(f"The number of microsteps that will be applied is {nb_steps}")
        if speed > self.pump_max_speed:
            speed = self.pump_max_speed
            loguru.logger.warning(
                f"Pump speed has been clamped to a maximum safe speed of {speed}mL/min"
            )
        steps_per_second = speed * self.pump_steps_per_ml * 256 / 60
        loguru.logger.debug(f"There will be a speed of {steps_per_second} steps per second")
        self.pump_stepper.speed = int(steps_per_second)

        if self.actuator_client:
            self.actuator_client.client.publish(
                "status/pump",
                f'{{"status":"Started", "duration":{nb_steps / steps_per_second}}}',
            )

        if direction == "FORWARD":
            self.pump_started = True
            self.pump_stepper.go(FORWARD, nb_steps)
            return

        if direction == "BACKWARD":
            self.pump_started = True
            self.pump_stepper.go(BACKWARD, nb_steps)
            return

    @loguru.logger.catch
    def run(self):
        loguru.logger.info(f"The stepper control process has been started in process {os.getpid()}")

        self.actuator_client = mqtt.MQTT_Client(topic="actuator/#", name="actuator_client")
        self.actuator_client.client.publish("status/pump", '{"status":"Ready"}')

        loguru.logger.success("The pump is READY!")
        while not self.stop_event.is_set():
            if self.actuator_client and self.actuator_client.new_message_received():
                self.treat_command()
            if self.pump_started and self.pump_stepper.at_goal():
                loguru.logger.success("The pump movement is over!")
                self.actuator_client.client.publish(
                    "status/pump",
                    '{"status":"Done"}',
                )
                self.pump_started = False
                self.pump_stepper.release()

            time.sleep(0.01)
        loguru.logger.info("Shutting down the stepper process")
        if self.actuator_client:
            self.actuator_client.client.publish("status/pump", '{"status":"Dead"}')
        self.pump_stepper.shutdown()

        if self.actuator_client:
            self.actuator_client.shutdown()
        loguru.logger.success("Stepper process shut down! See you!")


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
