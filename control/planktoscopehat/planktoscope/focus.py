"""
This module provides the functionality to control the focus mechanism
of the Planktoscope.
"""

# Libraries to control the steppers for focusing
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
    This class controls the stepper motor used for adjusting the focus.
    """

    def __init__(self, stepper, size):  # pylint: disable=unused-argument
        """
        Initialize the stepper class

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


class FocusProcess(multiprocessing.Process):
    """
    This class manages the focusing process using a stepper motor.
    """

    # 507 steps per ml for PlanktoScope standard
    focus_steps_per_mm = 40

    # focus max speed is in mm/sec and is limited by the maximum number of pulses per second the
    # PlanktoScope can send
    focus_max_speed = 5

    def __init__(self, event):
        """
        Initialize the FocusProcess.

        Args:
            event (multiprocessing.Event): Event to signal the process to stop.
        """
        super().__init__()
        loguru.logger.info("Initialising the stepper process")

        self.stop_event = event
        self.focus_started = False
        self.actuator_client: typing.Optional[mqtt.MQTT_Client] = (
            None  # Initialize actuator_client to None
        )

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
        self.focus_steps_per_mm = configuration.get("focus_steps_per_mm", self.focus_steps_per_mm)
        self.focus_max_speed = configuration.get("focus_max_speed", self.focus_max_speed)

        # define the names for the 2 exsting steppers
        if reverse:

            self.focus_stepper = Stepper(STEPPER1, size=45)
        else:

            self.focus_stepper = Stepper(STEPPER2, size=45)

        # Set stepper controller max speed

        self.focus_stepper.acceleration = 1000
        self.focus_stepper.deceleration = self.focus_stepper.acceleration
        self.focus_stepper.speed = self.focus_max_speed * self.focus_steps_per_mm * 256

        loguru.logger.info("the focus stepper initialisation is over")

    def __message_focus(self, last_message):
        """
        Handle a focusing request.

        Args:
            last_message (dict): The last received message.
        """
        loguru.logger.debug("We have received a focusing request")
        action = last_message.get("action")
        if action == "stop":
            self.__handle_stop_action()
        elif action == "move":
            self.__handle_move_action(last_message)
        else:
            loguru.logger.warning(f"The received message was not understood {last_message}")

    def __handle_stop_action(self):
        loguru.logger.debug("We have received a stop focus command")
        self.focus_stepper.shutdown()

        loguru.logger.info("The focus has been interrupted")
        if self.actuator_client:
            self.actuator_client.client.publish("status/focus", '{"status":"Interrupted"}')

    def __handle_move_action(self, last_message):
        loguru.logger.debug("We have received a move focus command")
        if "direction" not in last_message or "distance" not in last_message:
            loguru.logger.error(f"The received message has the wrong argument {last_message}")
            if self.actuator_client:
                self.actuator_client.client.publish("status/focus", '{"status":"Error"}')
            return
        direction = last_message["direction"]
        distance = float(last_message["distance"])
        speed = float(last_message["speed"]) if "speed" in last_message else 0

        loguru.logger.info("The focus movement is started.")
        if speed:
            self.focus(direction, distance, speed)
        else:
            self.focus(direction, distance)

    def treat_command(self):
        """
        Process a received command.
        """
        command = ""
        if not self.actuator_client:
            loguru.logger.error("Actuator client is not initialized")
            return

        loguru.logger.info("We received a new message")
        last_message = self.actuator_client.msg["payload"]  # type: ignore
        loguru.logger.debug(last_message)
        command = self.actuator_client.msg["topic"].split("/", 1)[1]  # type: ignore
        loguru.logger.debug(command)
        self.actuator_client.read_message()

        if command == "focus":
            self.__message_focus(last_message)
        elif command != "":
            loguru.logger.warning(
                f"We did not understand the received request {command} - {last_message}"
            )

    def focus(self, direction, distance, speed=focus_max_speed):
        """Moves the focus stepper

        direction is either UP or DOWN
        distance is received in mm
        speed is in mm/sec

        Args:
            direction (string): either UP or DOWN
            distance (int): distance to move the stage, in mm
            speed (int, optional): max speed of the stage, in mm/sec. Defaults to focus_max_speed.
        """

        loguru.logger.info(
            f"The focus stage will move {direction} for {distance}mm at {speed}mm/sec"
        )

        # Validation of inputs
        if direction not in ["UP", "DOWN"]:
            loguru.logger.error("The direction command is not recognised")
            loguru.logger.error("It should be either UP or DOWN")
            return

        if distance > 45:
            loguru.logger.error("You are trying to move more than the stage physical size")
            return

        # We are going to use 256 microsteps, so we need to multiply by 256 the steps number
        nb_steps = round(self.focus_steps_per_mm * distance * 256, 0)
        loguru.logger.debug(f"The number of microsteps that will be applied is {nb_steps}")
        if speed > self.focus_max_speed:
            speed = self.focus_max_speed
            loguru.logger.warning(
                f"Focus stage speed has been clamped to a maximum safe speed of {speed} mm/sec"
            )
        steps_per_second = speed * self.focus_steps_per_mm * 256
        loguru.logger.debug(f"There will be a speed of {steps_per_second} steps per second")
        self.focus_stepper.speed = int(steps_per_second)

        # Publish the status "Started" to via MQTT to Node-RED
        if self.actuator_client:
            self.actuator_client.client.publish(
                "status/focus",
                f'{{"status":"Started", "duration":{nb_steps / steps_per_second}}}',
            )

        # Depending on direction, select the right direction for the focus
        if direction == "UP":
            self.focus_started = True
            self.focus_stepper.go(FORWARD, nb_steps)
            return

        if direction == "DOWN":
            self.focus_started = True
            self.focus_stepper.go(BACKWARD, nb_steps)
            return

    # The pump max speed will be at about 400 full steps per second
    # This amounts to 0.9mL per seconds maximum, or 54mL/min
    # NEMA14 pump with 3 rollers is 0.509 mL per round, actual calculation at
    # Stepper is 200 steps/round, or 393steps/ml
    # https://www.wolframalpha.com/input/?i=pi+*+%280.8mm%29%C2%B2+*+54mm+*+3

    @loguru.logger.catch
    def run(self):
        loguru.logger.info(f"The stepper control process has been started in process {os.getpid()}")

        # Creates the MQTT Client
        self.actuator_client = mqtt.MQTT_Client(topic="actuator/#", name="actuator_client")
        if self.actuator_client:
            self.actuator_client.client.publish("status/focus", '{"status":"Ready"}')

        loguru.logger.success("Stepper is READY!")
        while not self.stop_event.is_set():
            if self.actuator_client.new_message_received():
                self.treat_command()
            if self.focus_started and self.focus_stepper.at_goal():
                loguru.logger.success("The focus movement is over!")
                self.actuator_client.client.publish(
                    "status/focus",
                    '{"status":"Done"}',
                )
                self.focus_started = False
                self.focus_stepper.release()
            time.sleep(0.01)

        loguru.logger.info("Shutting down the stepper process")
        if self.actuator_client:
            self.actuator_client.client.publish("status/focus", '{"status":"Dead"}')
        self.focus_stepper.shutdown()

        if self.actuator_client:
            self.actuator_client.shutdown()
        loguru.logger.success("Stepper process shut down! See you!")


# This is called if this script is launched directly
if __name__ == "__main__":
    # TODO This should be a test suite for this library
    # Starts the stepper thread for actuators
    # This needs to be in a threading or multiprocessing wrapper
    stop_event = multiprocessing.Event()
    focus_thread = FocusProcess(event=stop_event)
    focus_thread.start()
    try:
        focus_thread.join()
    except KeyboardInterrupt:
        stop_event.set()
        focus_thread.join()
