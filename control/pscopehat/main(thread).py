import sys
import multiprocessing
import time
import signal  # for handling SIGINT/SIGTERM
import os

from loguru import logger

import planktoscope.mqtt
import planktoscope.stepper
import planktoscope.imagernew
import planktoscope.light # Fan HAT LEDs
import planktoscope.identity
import planktoscope.uuidName # Note: this is deprecated.
import planktoscope.display # Fan HAT OLED screen

# enqueue=True is necessary so we can log accross modules
# rotation happens everyday at 01:00 if not restarted
logger.add(
    # sys.stdout,
    "/home/pi/device-backend-logs/control/{time}.log",
    rotation="5 MB",
    retention="1 week",
    compression=".tar.gz",
    enqueue=True,
    level="DEBUG",
)

# The available level for the logger are as follows:
# Level name 	Severity 	Logger method
# TRACE 	    5 	        logger.trace()
# DEBUG 	    10 	        logger.debug()
# INFO 	        20 	        logger.info()
# SUCCESS 	    25 	        logger.success()
# WARNING 	    30      	logger.warning()
# ERROR 	    40       	logger.error()
# CRITICAL 	    50      	logger.critical()

logger.info("Starting the PlanktoScope python script!")

run = True  # global variable to enable clean shutdown from stop signals

def handler_stop_signals(signum, frame):
    """This handler simply stop the forever running loop in __main__"""
    global run
    logger.info(f"Received a signal asking to stop {signum}")
    run = False


if __name__ == "__main__":
    logger.info("Welcome!")
    logger.info("Initialising signals handling and sanitizing the directories (step 1/5)")
    signal.signal(signal.SIGINT, handler_stop_signals)
    signal.signal(signal.SIGTERM, handler_stop_signals)

    # check if gpu_mem configuration is at least 256Meg, otherwise the camera will not run properly
    with open("/boot/config.txt", "r") as config_file:
        for i, line in enumerate(config_file):
            if line.startswith("gpu_mem") and int(line.split("=")[1].strip()) < 256:
                logger.error(
                    "The GPU memory size is less than 256, this will prevent the camera from running properly"
                )
                logger.error(
                    "Please edit the file /boot/config.txt to change the gpu_mem value to at least 256"
                )
                logger.error(
                    "or use raspi-config to change the memory split, in menu 7 Advanced Options, A3 Memory Split"
                )
                sys.exit(1)

    # Let's make sure the used base path exists
    img_path = "/home/pi/PlanktoScope/img"  # FIXME: this path is incorrect - why doesn't it cause side effects?
    # check if this path exists
    if not os.path.exists(img_path):
        # create the path!
        os.makedirs(img_path)

    logger.info(f"This PlanktoScope's Raspberry Pi's serial number is {planktoscope.uuidName.getSerial()}")
    logger.info(
        f"This PlanktoScope's machine name is {planktoscope.identity.load_machine_name()}"
    )
    logger.info(
        f"This PlanktoScope's deprecated name is {planktoscope.uuidName.machineName(machine=planktoscope.uuidName.getSerial())}"
    )

    # Prepare the event for a gracefull shutdown
    shutdown_event = multiprocessing.Event()
    shutdown_event.clear()

    # Starts the stepper process for actuators
    logger.info("Starting the stepper control process (step 2/5)")
    stepper_thread = planktoscope.stepper.StepperProcess(shutdown_event)
    stepper_thread.start()

    # TODO try to isolate the imager thread (or another thread)
    # Starts the imager control process
    logger.info("Starting the imager control process (step 3/5)")
    try:
        imager_thread = planktoscope.imagernew.ImagerProcess(shutdown_event)
    except:
        logger.error("The imager control process could not be started")
        imager_thread = None
    else:
        imager_thread.start()

    # Starts the light process
    logger.info("Starting the light control process (step 4/5)")
    try:
        light_thread = planktoscope.light.LightProcess(shutdown_event)
    except Exception as e:
        logger.error("The light control process could not be started")
        light_thread = None
    else:
        light_thread.start()

    logger.info("Starting the display control (step 5/5)")
    display = planktoscope.display.Display()

    logger.success("Looks like everything is set up and running, have fun!")

    while run:
        # TODO look into ways of restarting the dead threads
        logger.trace("Running around in circles while waiting for someone to die!")
        if not stepper_thread.is_alive():
            logger.error("The stepper process died unexpectedly! Oh no!")
            break
        if not imager_thread or not imager_thread.is_alive():
            logger.error("The imager process died unexpectedly! Oh no!")
            break
        time.sleep(1)

    display.display_text("Bye Bye!")
    logger.info("Shutting down the shop")
    shutdown_event.set()
    time.sleep(1)

    stepper_thread.join()
    if imager_thread:
        imager_thread.join()
    if light_thread:
        light_thread.join()

    stepper_thread.close()
    if imager_thread:
        imager_thread.close()
    if light_thread:
        light_thread.close()

    display.stop()

    logger.info("Bye")
