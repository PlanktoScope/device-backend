"""
This module defines the EEPROM class for interfacing with an EEPROM device using I2C.
It includes methods for writing, reading, and editing data on the EEPROM.
"""

import time

import gpiozero  # type: ignore
import smbus  # type: ignore


class EEPROM:
    """Class for interfacing with an EEPROM device using I2C."""
    MAX_BLOCK_SIZE: int = 32  # EEPROM page limit

    def __init__(self, eeprom_address: int = 0x50, i2c_bus: int = 0, gpio_pin: int = 4) -> None:
        # Initialize EEPROM with specified I2C address, bus, and GPIO pin for write control
        self._eeprom_address: int = eeprom_address  # EEPROM I2C address
        self._i2c_bus: int = i2c_bus  # I2C bus number
        self._gpio_pin: int = gpio_pin  # GPIO pin number for write control
        self._bus = smbus.SMBus(i2c_bus)  # Set up I2C bus
        # Set up GPIO for write control
        self._write_control = gpiozero.OutputDevice(gpio_pin, active_high=True)

    def write_on_eeprom(self, start_addr: list[int], data: dict[str, str]) -> None:
        """Writes data to the EEPROM starting from specified addresses."""
        values = list(data.values())  # Convert data dictionary values into a list

        for i, value in enumerate(values):
            current_addr = start_addr[i]  # Starting address for this data segment
            data_to_write = [ord(char) for char in value]  # Convert each character to ASCII
            remaining_data = data_to_write  # Data remaining to be written

            while remaining_data:
                # Ensure data doesn't exceed page boundaries
                page_boundary = self.MAX_BLOCK_SIZE - (current_addr % self.MAX_BLOCK_SIZE)
                write_length = min(len(remaining_data), page_boundary)
                mem_addr_high = (current_addr >> 8) & 0xFF  # High byte of memory address
                mem_addr_low = current_addr & 0xFF  # Low byte of memory address

                self._write_control.off()  # Enable writing by setting write control low
                try:
                    # Write data block to EEPROM
                    self._bus.write_i2c_block_data(
                        self._eeprom_address,
                        mem_addr_high,
                        [mem_addr_low] + remaining_data[:write_length],
                    )
                    time.sleep(0.01)  # Pause to ensure data stability

                    remaining_data = remaining_data[write_length:]  # Update remaining data
                    current_addr += write_length  # Update current address
                except IOError as e:
                    print(f"Error during the writing process at address {current_addr:#04x}: {e}")
                finally:
                    self._write_control.on()  # Disable writing
                    time.sleep(0.01)

    def read_data_eeprom(self, start_addr: list[int], data_lengths: list[int]) -> list[str]:
        """Reads data from the EEPROM from specified starting addresses and lengths."""
        all_data: list[str] = []  # Container for the data read from EEPROM

        for i, start in enumerate(start_addr):
            mem_addr_high = (start >> 8) & 0xFF  # High byte of start address
            mem_addr_low = start & 0xFF  # Low byte of start address
            length = data_lengths[i]  # Length of data to read for this segment

            try:
                # Set the memory address to start reading from
                self._bus.write_byte_data(self._eeprom_address, mem_addr_high, mem_addr_low)
                time.sleep(0.01)

                data: list[int] = []  # List to hold read bytes
                for _ in range(length):
                    byte = self._bus.read_byte(self._eeprom_address)  # Read each byte
                    data.append(byte)
                    time.sleep(0.01)

                # Convert byte list to string, ignoring null bytes
                result = "".join([chr(byte) for byte in data if byte != 0x00])
                all_data.append(result)  # Append result to all_data
            except IOError as e:
                print(f"Error during the reading process : {e}")

        return all_data  # Return all read data

    def edit_eeprom(
        self,
        data: dict[str, str],
        labels: list[str],
        start_addr: list[int],
        data_lengths: list[int]
    ) -> None:
        # Edit specific data in EEPROM based on labels, starting addresses, and lengths
        keys = list(data.keys())  # List of keys in data dictionary

        for _, key in enumerate(keys):
            if key in labels:
                # Find index of label in labels list and corresponding address and length
                label_index = labels.index(key)
                current_addr = start_addr[label_index]
                value = data[key]
                data_to_write = [ord(char) for char in value]  # Convert value to ASCII codes
                remaining_data = data_to_write
                data_length = data_lengths[label_index]  # Maximum length allowed for this data

                # Read current EEPROM data at this address for comparison
                try:
                    self._bus.write_byte_data(
                        self._eeprom_address, (current_addr >> 8) & 0xFF, current_addr & 0xFF
                    )
                    time.sleep(0.01)
                except IOError as e:
                    print(f"Error during the reading process at address {current_addr:#04x}: {e}")
                    continue

                # If new data is shorter than required length, pad with null bytes
                if len(data_to_write) < data_length:
                    data_to_write.extend([0x00] * (data_length - len(data_to_write)))

                # Begin writing process, ensuring page boundaries are respected
                while remaining_data:
                    page_boundary = self.MAX_BLOCK_SIZE - (current_addr % self.MAX_BLOCK_SIZE)
                    write_length = min(len(remaining_data), page_boundary)
                    mem_addr_high, mem_addr_low = self._get_memory_address_bytes(current_addr)

                    self._write_control.off()  # Enable writing
                    try:
                        # Write the data to EEPROM
                        self._bus.write_i2c_block_data(
                            self._eeprom_address,
                            mem_addr_high,
                            [mem_addr_low] + remaining_data[:write_length],
                        )
                        time.sleep(0.01)

                        remaining_data = remaining_data[write_length:]  # Update remaining data
                        current_addr += write_length  # Move to next address
                    except IOError as e:
                        print(f"Error during the writing process: {e}")
                    finally:
                        self._write_control.on()  # Disable writing
                        time.sleep(0.01)

    def _get_memory_address_bytes(self, address: int) -> tuple[int, int]:
        """Returns the high and low bytes of a 16-bit memory address."""
        mem_addr_high = (address >> 8) & 0xFF  # High byte of the address
        mem_addr_low = address & 0xFF  # Low byte of the address
        return mem_addr_high, mem_addr_low
