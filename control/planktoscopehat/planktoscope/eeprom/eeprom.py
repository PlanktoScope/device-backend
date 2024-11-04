import time
from typing import Dict, List

import smbus  # type: ignore
from gpiozero import OutputDevice  # type: ignore


class EEPROM:
    MAX_BLOCK_SIZE: int = 32  # Maximum number of bytes that can be written in one page on the EEPROM

    def __init__(self, eeprom_address: int = 0x50, i2c_bus: int = 0, gpio_pin: int = 4) -> None:
        # Initialize EEPROM with specified I2C address, bus, and GPIO pin for write control
        self._eeprom_address: int = eeprom_address  # EEPROM I2C address
        self._i2c_bus: int = i2c_bus  # I2C bus number
        self._gpio_pin: int = gpio_pin  # GPIO pin number for write control
        self._bus = smbus.SMBus(i2c_bus)  # Set up I2C bus
        self._write_control = OutputDevice(
            gpio_pin, active_high=True
        )  # Set up GPIO for write control

    def _write_on_eeprom(self, start_addr: List[int], data: Dict[str, str]) -> None:
        # Write data to EEPROM starting from specified addresses
        values = list(data.values())  # Convert data dictionary values into a list

        for i in range(len(values)):
            current_addr = start_addr[i]  # Starting address for this data segment
            data_to_write = [ord(char) for char in values[i]]  # Convert each character to ASCII
            remaining_data = data_to_write  # Data remaining to be written

            while remaining_data:
                # Ensure data doesn't cross page boundaries by limiting the write length to MAX_BLOCK_SIZE
                page_boundary = self.MAX_BLOCK_SIZE - (current_addr % self.MAX_BLOCK_SIZE)
                write_length = min(len(remaining_data), page_boundary)  # Write up to the page boundary
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
                except Exception as e:
                    print(f"Error during the writing process at address {current_addr:#04x}: {e}")
                finally:
                    self._write_control.on()  # Disable writing
                    time.sleep(0.01)

    def _read_data_eeprom(self, start_addr: List[int], data_lengths: List[int]) -> List[str]:
        # Read data from EEPROM starting from specified addresses and for specified lengths
        all_data: List[str] = []  # Container for the data read from EEPROM

        for i in range(len(start_addr)):
            mem_addr_high = (start_addr[i] >> 8) & 0xFF  # High byte of start address
            mem_addr_low = start_addr[i] & 0xFF  # Low byte of start address
            length = data_lengths[i]  # Length of data to read for this segment

            try:
                # Set the memory address to start reading from
                self._bus.write_byte_data(self._eeprom_address, mem_addr_high, mem_addr_low)
                time.sleep(0.01)

                data: List[int] = []  # List to hold read bytes
                for _ in range(length):
                    byte = self._bus.read_byte(self._eeprom_address)  # Read each byte
                    data.append(byte)
                    time.sleep(0.01)

                # Convert byte list to string, ignoring null bytes
                result = "".join([chr(byte) for byte in data if byte != 0x00])
                all_data.append(result)  # Append result to all_data
            except Exception as e:
                print(f"Error during the reading process starting from address {start_addr[i]:#04x} : {e}")

        return all_data  # Return all read data

    def _edit_eeprom(
        self, data: Dict[str, str], labels: List[str], start_addr: List[int], data_lengths: List[int]
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
                    current_data = [
                        self._bus.read_byte(self._eeprom_address) for _ in range(data_length)
                    ]
                except Exception as e:
                    print(f"Error during the reading process at address {current_addr:#04x}: {e}")
                    continue

                # If new data is shorter than required length, pad with null bytes
                if len(data_to_write) < data_length:
                    extra_bytes_start = len(data_to_write)
                    data_to_write.extend([0x00] * (data_length - len(data_to_write)))  # Pad with 0x00

                # Begin writing process, ensuring page boundaries are respected
                while remaining_data:
                    page_boundary = self.MAX_BLOCK_SIZE - (current_addr % self.MAX_BLOCK_SIZE)
                    write_length = min(len(remaining_data), page_boundary)
                    mem_addr_high = (current_addr >> 8) & 0xFF
                    mem_addr_low = current_addr & 0xFF

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
                    except Exception as e:
                        print(f"Error during the writing process at address {current_addr:#04x}: {e}")
                    finally:
                        self._write_control.on()  # Disable writing
                        time.sleep(0.01)