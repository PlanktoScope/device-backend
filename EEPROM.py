import smbus2 as smbus
import RPi.GPIO as GPIO
import time

# I2C address and bus
EEPROM_I2C_ADDRESS = 0x50  # Assumed address
I2C_BUS = 4
WC_PIN = 14

# Initialize I2C bus
bus = smbus.SMBus(I2C_BUS)

# Initialize GPIO
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(WC_PIN, GPIO.OUT, initial=GPIO.HIGH)  # Configure WC_PIN as output, initially HIGH

# Function to read a byte from EEPROM
def read_eeprom_byte(address):
    try:
        return bus.read_byte_data(EEPROM_I2C_ADDRESS, address)
    except Exception as e:
        print(f"Error reading EEPROM: {e}")
        return None

# Function to write a byte to EEPROM
def write_eeprom_byte(address, data):
    try:
        GPIO.output(WC_PIN, GPIO.LOW)  # Enable write operations
        bus.write_byte_data(EEPROM_I2C_ADDRESS, address, data)
        time.sleep(0.05)  # Ensure write cycle is complete
        GPIO.output(WC_PIN, GPIO.HIGH)  # Disable write operations
    except Exception as e:
        print(f"Error writing to EEPROM: {e}")

# Address to read/write
eeprom_address = 0x00

# Data to write (example data)
data_to_write = 0xA5

# Write data to EEPROM
write_eeprom_byte(eeprom_address, data_to_write)
print("Byte written to EEPROM.")

# Read back the data
read_data = read_eeprom_byte(eeprom_address)
print("Byte read from EEPROM:")
print(hex(read_data) if read_data is not None else "Read error")
