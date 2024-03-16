import threading
import time

import loguru


class PicamThread(threading.Thread):
    """This class contains the main definitions of picamera thread"""

    def __init__(self, camera, command_queue, stop_event):
        """Initialize the picamera thread class

        Args:
            camera: picamera instance
            command_queue (queue.Queue): queue for commands, when info must be exchanged safely
            between several threads
            stop_event (multiprocessing.Event or threading.Event): shutdown event
        """
        super().__init__()
        self.__picam = camera
        self.command_queue = command_queue  # FIXME remove the queue for now if not used
        self.stop_event = stop_event

    @loguru.logger.catch
    def run(self):
        try:
            self.__picam.start()
        except Exception as e:
            loguru.logger.exception(f"An exception has occured when starting up picamera2: {e}")
            try:
                self.__picam.start(True)
            except Exception as e_second:
                loguru.logger.exception(
                    f"A second exception has occured when starting up picamera2: {e_second}"
                )
                loguru.logger.error("This error can't be recovered from, terminating now")
                raise e_second
        try:
            while not self.stop_event.is_set():
                # if not self.command_queue.empty():
                # try:
                #    # Retrieve a command from the queue with a timeout to avoid indefinite blocking
                #    command = self.command_queue.get(timeout=0.1)
                # except Exception as e:
                #    loguru.logger.exception(f"An error has occurred while handling a command: {e}")
                time.sleep(0.01)
        finally:
            self.__picam.stop()
            self.__picam.close()
