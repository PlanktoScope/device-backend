import shush.boards.pscope_hat_0_1 as s1
import spidev
from gpiozero import DigitalOutputDevice, DigitalInputDevice

class Board:
    def __init__(self):
        # Initialize the peripherals (SPI and GPIO)
        self.init_spi()
        self.init_gpio_state()

    def init_gpio_state(self):
        # Sets the default states for the GPIO on the Shush modules.
        
        # Chip Select pins (set initially high, inactive)
        self.m0_cs = DigitalOutputDevice(s1.m0_cs, initial_value=True)
        self.m1_cs = DigitalOutputDevice(s1.m1_cs, initial_value=True)

        # Error and Stall pins (input pins to monitor)
        self.error = DigitalInputDevice(s1.error)
        self.stall = DigitalInputDevice(s1.stall)

        # Enable pins for motors (set initially low, inactive)
        self.m0_enable = DigitalOutputDevice(s1.m0_enable, initial_value=False)
        self.m1_enable = DigitalOutputDevice(s1.m1_enable, initial_value=False)

    def init_spi(self):
        # Initialize SPI Bus for motor drivers.

        # SPI for motor 0
        Board.spi0 = spidev.SpiDev()
        Board.spi0.open(0, 0)
        Board.spi0.max_speed_hz = 1000000
        Board.spi0.bits_per_word = 8
        Board.spi0.loop = False
        Board.spi0.mode = 3

        # SPI for motor 1
        Board.spi1 = spidev.SpiDev()
        Board.spi1.open(0, 1)
        Board.spi1.max_speed_hz = 1000000
        Board.spi1.bits_per_word = 8
        Board.spi1.loop = False
        Board.spi1.mode = 3

    # def __del__(self):
    #     # Cleanup for DigitalOutputDevice and DigitalInputDevice pins
    #     self.m0_cs.close()
    #     self.m1_cs.close()
    #     self.error.close()
    #     self.stall.close()
    #     self.m0_enable.close()
    #     self.m1_enable.close()
