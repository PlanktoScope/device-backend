################################################################################
# Practical Libraries
################################################################################
# Logger library compatible with multiprocessing
from loguru import logger
import os, time
# Library for starting processes
import multiprocessing
# Basic planktoscope libraries
import planktoscope.mqtt
import RPi.GPIO
import subprocess  # nosec
# Library to send command over I2C for the light module on the fan
import smbus2 as smbus
import enum

logger.info("planktoscope.light is loaded")

def i2c_update():
    # Update the I2C Bus in order to really update the LEDs new values
    subprocess.Popen("i2cdetect -y 1".split(), stdout=subprocess.PIPE)  # nosec
class i2c_led:
    """
    LM36011 Led controller
    """

    @enum.unique
    class Register(enum.IntEnum):
        enable = 0x01
        configuration = 0x02
        flash = 0x03
        torch = 0x04
        flags = 0x05
        id_reset = 0x06

    DEVICE_ADDRESS = 0x64
    # This constant defines the current (mA) sent to the LED, 10 allows the use of the full ISO scale and results in a voltage of 2.77v
    DEFAULT_CURRENT = 10

    LED_selectPin = 18

    def __init__(self):
        self.VLED_short = False
        self.thermal_scale = False
        self.thermal_shutdown = False
        self.UVLO = False
        self.flash_timeout = False
        self.IVFM = False
        RPi.GPIO.setwarnings(False)
        RPi.GPIO.setmode(RPi.GPIO.BCM)
        RPi.GPIO.setup(self.LED_selectPin, RPi.GPIO.OUT)
        self.output_to_led1()
        self.on = False
        try:
            self.force_reset()
            if self.get_flags():
                logger.error("Flags raised in the LED Module, clearing now")
                self.VLED_short = False
                self.thermal_scale = False
                self.thermal_shutdown = False
                self.UVLO = False
                self.flash_timeout = False
                self.IVFM = False
            led_id = self.get_id()
        except (OSError, Exception) as e:
            logger.exception(f"Error with the LED control module, {e}")
            raise
        logger.debug(f"LED module id is {led_id}")

    def output_to_led1(self):
        logger.debug("Switching output to LED 1")
        RPi.GPIO.output(self.LED_selectPin, RPi.GPIO.HIGH)

    def get_id(self):
        led_id = self._read_byte(self.Register.id_reset)
        led_id = led_id & 0b111111
        return led_id

    def get_state(self):
        return self.on

    def force_reset(self):
        logger.debug("Resetting the LED chip")
        self._write_byte(self.Register.id_reset, 0b10000000)

    def get_flags(self): # this method checks the state of the LED and logs it out 
        flags = self._read_byte(self.Register.flags)
        self.flash_timeout = bool(flags & 0b1)
        self.UVLO = bool(flags & 0b10)
        self.thermal_shutdown = bool(flags & 0b100)
        self.thermal_scale = bool(flags & 0b1000)
        self.VLED_short = bool(flags & 0b100000)
        self.IVFM = bool(flags & 0b1000000)
        if self.VLED_short:
            logger.warning("Flag VLED_Short asserted")
        if self.thermal_scale:
            logger.warning("Flag thermal_scale asserted")
        if self.thermal_shutdown:
            logger.warning("Flag thermal_shutdown asserted")
        if self.UVLO:
            logger.warning("Flag UVLO asserted")
        if self.flash_timeout:
            logger.warning("Flag flash_timeout asserted")
        if self.IVFM:
            logger.warning("Flag IVFM asserted")
        return flags


    def set_torch_current(self, current):
        # From 3 to 376mA
        # Curve is not linear for some reason, but this is close enough
        if current > 376:
            raise ValueError("the chosen current is too high, max value is 376mA")
        value = int(current * 0.34)
        logger.debug(
            f"Setting torch current to {current}mA, or integer {value} in the register"
        )
        try:
            self._write_byte(self.Register.torch, value)
        except Exception as e:
            logger.exception(f"Error with the LED control module, {e}")
            raise

    def activate_torch(self):
        logger.debug("Activate torch")
        self._write_byte(self.Register.enable, 0b10)
        self.on = True

    def deactivate_torch(self):
        logger.debug("Deactivate torch")
        self._write_byte(self.Register.enable, 0b00)
        self.off = False


    def _write_byte(self, address, data):
        with smbus.SMBus(1) as bus:
            bus.write_byte_data(self.DEVICE_ADDRESS, address, data)

    def _read_byte(self, address):
        with smbus.SMBus(1) as bus:
            b = bus.read_byte_data(self.DEVICE_ADDRESS, address)
        return b
################################################################################
# Main Segmenter class
################################################################################
class LightProcess(multiprocessing.Process):
    """This class contains the main definitions for the light of the PlanktoScope"""

    def __init__(self, event):
        """Initialize the Light class

        Args:
            event (multiprocessing.Event): shutdown event
        """
        super(LightProcess, self).__init__(name="light")

        logger.info("planktoscope.light is initialising")

        self.stop_event = event
        self.light_client = None
        try:
            self.led = i2c_led()
            self.led.output_to_led1()
            self.led.activate_torch()
            time.sleep(0.5)
            self.led.deactivate_torch()
            self.led.output_to_led1()

        except Exception as e:
            logger.error(
                f"We have encountered an error trying to start the LED module, stopping now, exception is {e}"
            )
            raise e
        else:
            logger.success("planktoscope.light is initialised and ready to go!")

    def led_off(self, led):
        if led == 0:
            logger.debug("Turning led 1 off")
        self.led.deactivate_torch()

    def led_on(self, led):
        if led not in [0]:
            raise ValueError("Led number is wrong")
        if led == 0:
            logger.debug("Turning led 1 on")
            self.led.output_to_led1()
        self.led.activate_torch()


    @logger.catch
    def treat_message(self):
        if self.light_client.new_message_received():
            logger.info("We received a new message")
            last_message = self.light_client.msg["payload"]
            logger.debug(last_message)
            self.light_client.read_message()
            action = last_message.get("action")
            led = last_message.get("led", 1)
            if action == "on" and led == 1:
                self.led_on(0)
                self.light_client.client.publish("status/light", '{"status":"Led 1: On"}')
            elif action == "off" and led == 1:
                self.led_off(0)
                self.light_client.client.publish("status/light", '{"status":"Led 1: Off"}')
            else:
                self.light_client.client.publish("status/light", '{"status":"Invalid action or LED number"}')


    ################################################################################
    # While loop for capturing commands from Node-RED
    ################################################################################
    @logger.catch
    def run(self):
        """This is the function that needs to be started to create a thread"""
        logger.info(
            f"The light control thread has been started in process {os.getpid()}"
        )
        # MQTT Service connection
        self.light_client = planktoscope.mqtt.MQTT_Client(
            topic="light", name="light_client"
        )
        # Publish the status "Ready" to via MQTT to Node-RED
        self.light_client.client.publish("status/light", '{"status":"Ready"}')
        logger.success("Light module is READY!")
        # This is the loop
        while not self.stop_event.is_set():
            self.treat_message()
            time.sleep(0.1)
        logger.info("Shutting down the light process")
        self.led.deactivate_torch()
        self.led.set_torch_current(1)

        self.led.get_flags()
        RPi.GPIO.cleanup()
        self.light_client.client.publish("status/light", '{"status":"Dead"}')
        self.light_client.shutdown()
        logger.success("Light process shut down! See you!")