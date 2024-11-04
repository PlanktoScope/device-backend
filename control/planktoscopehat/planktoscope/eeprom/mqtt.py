"""
This module manages the MQTT communication for handling EEPROM operations,
interfacing between Node-RED and EEPROM through a Worker class.
"""

import datetime
import json
import threading
import time
from typing import Optional

import loguru

from planktoscope import mqtt
from planktoscope.eeprom import eeprom


class Worker(threading.Thread):
    """
    A Worker class for handling EEPROM operations via MQTT.
    This class communicates between Node-RED and the EEPROM, allowing data to be
    written, edited, or read based on MQTT messages received.
    """
    
    LABELS: list[str] = [
        "eeprom_planktoscope_ref",
        "acq_planktoscope_sn",
        "acq_planktoscope_version",
        "acq_planktoscope_date_factory",
        "acq_hat_sn",
        "acq_hat_version",
        "eeprom_driver_ref",
        "eeprom_pump_ref",
        "eeprom_focus_ref",
        "eeprom_obj_lens_ref",
        "eeprom_tube_lens_ref",
        "eeprom_flowcell_thickness",
        "eeprom_led_ref",
    ]
    START_ADDRESS: list[int] = [
        0x0000, 0x000C, 0x0012, 0x0018, 0x0022, 0x002F, 0x0035, 0x0041,
        0x004D, 0x0059, 0x0065, 0x0071, 0x007D,
    ]
    DATA_LENGTHS: list[int] = [12, 6, 6, 8, 10, 6, 12, 12, 12, 12, 12, 12, 12]

    def __init__(self) -> None:
        """Initialize the Worker for EEPROM operations."""
        super().__init__(name="eeprom_worker")
        self._eeprom = eeprom.EEPROM()
        self._stop_event = threading.Event()

    @loguru.logger.catch
    def run(self) -> None:
        """Start the worker thread and run the main event loop for MQTT and EEPROM operations."""
        self._mqtt = mqtt.MQTT_Client(topic="eeprom/#", name="eeprom_client")
        loguru.logger.info(
            "Worker started. Waiting for incoming MQTT messages and handling EEPROM operations..."
        )

        try:
            while not self._stop_event.is_set():
                if self._mqtt.new_message_received():
                    loguru.logger.info(f"Message received on MQTT: {self._mqtt.msg}")
                    self._handle_new_message()
                    self._mqtt.read_message()
                time.sleep(0.1)
        finally:
            loguru.logger.info("Stopping the MQTT API...")
            self.shutdown()

    @loguru.logger.catch
    def _handle_new_message(self) -> None:
        """Handle a new message received over MQTT."""
        if self._mqtt.msg is None:
            return

        if not self._mqtt.msg["topic"].startswith("eeprom/"):
            self._mqtt.read_message()
            return

        latest_message: dict[str, str] = self._mqtt.msg["payload"]
        hardware_info: str | dict[str, str] = latest_message.get("hardware_information", {})
        if not isinstance(hardware_info, dict):
            hardware_info = {}

        action: Optional[str] = latest_message.get("action")
        loguru.logger.debug(f"Action received: {action}")

        if action == "write_eeprom":
            self._process_write(hardware_info)
        elif action == "edit_eeprom":
            self._process_edit(latest_message)
        elif action == "read_eeprom":
            self._process_read()
        else:
            loguru.logger.error(f"Unknown action received: {action}")

    def _process_write(self, message: dict[str, str]) -> None:
        """Processes the received MQTT message and writes it to EEPROM."""
        try:
            data_received = {key: value for key, value in message.items() if key != "action"}
            if len(data_received) != 13:
                loguru.logger.error("Received data is missing some elements")
                self._mqtt.client.publish("status/eeprom", '{"status":"Missing data error"}')
            else:
                formatted_data = self._convert_date_format(data_received)
                self._eeprom.write_on_eeprom(self.START_ADDRESS, formatted_data)
                loguru.logger.success("Data written to EEPROM successfully.")
                self._mqtt.client.publish("status/eeprom", '{"status":"Data written"}')
        except KeyError as e:
            loguru.logger.error(f"Error in processing message: {e}")
            self._mqtt.client.publish("status/eeprom", '{"status":"Processing error"}')

    def _process_edit(self, message: dict[str, str]) -> None:
        """Processes the received MQTT message and writes it to EEPROM."""
        try:
            self._eeprom.edit_eeprom(
                message, self.LABELS, self.START_ADDRESS, self.DATA_LENGTHS
            )
            loguru.logger.success("Data edited successfully.")
            self._mqtt.client.publish("status/eeprom", '{"status":"Data updated"}')
        except Exception as e:
            loguru.logger.error(f"Unexpected error during message processing: {e}")
            self._mqtt.client.publish("status/eeprom", '{"status":"Processing error"}')

    def _process_read(self) -> None:
        """Reads data from EEPROM and sends it to the MQTT topic."""
        try:
            values = self._eeprom.read_data_eeprom(self.START_ADDRESS, self.DATA_LENGTHS)
            data_dict = dict(zip(self.LABELS, values))
            data_to_send = json.dumps(data_dict)
            self._mqtt.client.publish("eeprom/read_eeprom", data_to_send)
            loguru.logger.success(
                f"Data sent to MQTT on topic hardware/read_eeprom: {data_to_send}"
            )
            self._mqtt.client.publish("status/eeprom", '{"status":"Data read"}')
        except Exception as e:
            loguru.logger.error(f"Failed to read EEPROM and send data: {e}")
            self._mqtt.client.publish("status/eeprom", '{"status":"Reading error"}')

    def shutdown(self) -> None:
        """Stops the worker thread."""
        loguru.logger.info("Shutting down worker thread...")
        self._stop_event.set()
        self._mqtt.shutdown()
        loguru.logger.success("Worker thread stopped.")

    def _convert_date_format(self, data: dict[str, str]) -> dict[str, str]:
        """Converts the 'acq_planktoscope_date_factory' in the data received
        from 'YYYY/MM/DD' format to 'YYYYMMDD' format."""
        date_obj = datetime.datetime.strptime(data["acq_planktoscope_date_factory"], "%Y/%m/%d")
        formatted_date = date_obj.strftime("%Y%m%d")
        data["acq_planktoscope_date_factory"] = formatted_date
        return data
