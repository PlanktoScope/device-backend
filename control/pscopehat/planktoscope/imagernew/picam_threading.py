################################################################################
# Practical Libraries
################################################################################

# Library to call and control the camera
import planktoscope.imagernew.picamera

# Library to execute picamera in a separate thread within the imager process
import threading

# Logger library compatible with multiprocessing
from loguru import logger

# Library to create a queue for commands coming to the camera
import queue

# Library to manage time commands and delay execution for a given time
import time

################################################################################
# Class for the implementation of picamera2 thread
################################################################################

# Fonction qui sera exécutée dans un thread séparé pour gérer la caméra
def camera_thread(camera_queue, stop_event):
    picam2 = planktoscope.imagernew.picamera.picamera()
    picam2.configure(picam2.create_still_configuration())
    picam2.start_preview()
    
    try:
        while not stop_event.is_set():
            if not camera_queue.empty():
                command = camera_queue.get()
                if command == 'capture':
                    picam2.capture_file('/path/to/image.jpg')
                    # Vous pouvez également traiter la photo ou passer le chemin de l'image via la queue.
                # Ajoutez d'autres commandes si nécessaire
            time.sleep(0.01)
    finally:
        picam2.stop_preview()
        picam2.close()

# C'est là que votre processus principal commence
if __name__ == '__main__':
    # Créer une queue thread-safe pour les commandes
    camera_queue = queue.Queue()
    
    # Créer un événement pour signaler au thread quand s'arrêter
    stop_event = threading.Event()
    
    # Démarrer le thread de la caméra
    camera_threading = threading.Thread(target=camera_thread, args=(camera_queue, stop_event))
    camera_threading.start()
    
    # Faire d'autres traitements dans le processus principal ici...
    # Pour envoyer une commande au thread de la caméra :
    camera_queue.put('capture')

    # Quand vous avez terminé avec le thread de la caméra, demandez-lui de s'arrêter
    stop_event.set()
    camera_threading.join()

    # Le processus principal continue ou se termine ici

##############################################################################################################################

class CameraThread(threading.Thread):
    def __init__(self, camera, command_queue, stop_event):
        super().__init__()
        self.camera = camera
        self.command_queue = command_queue
        self.stop_event = stop_event

    @logger.catch
    def run(self):
        try:
            self.camera.start()  # Supposons que cela initialise et démarre la caméra
            while not self.stop_event.is_set():
                try:
                    # Récupérer une commande depuis la queue avec un timeout pour éviter de bloquer indéfiniment
                    command, args, kwargs = self.command_queue.get(timeout=0.1)
                    getattr(self.camera, command)(*args, **kwargs)
                except queue.Empty:
                    pass  # Continue la boucle si aucune commande n'est reçue
                except Exception as e:
                    logger.exception(f"Une erreur s'est produite lors de la manipulation d'une commande : {e}")
        finally:
            self.camera.close()


# Création de la file d'attente pour les commandes et l'événement d'arrêt 
command_queue = queue.Queue()
stop_event = threading.Event()

# Initialiser l'instance de la classe picamera
output_path = "output_video.h264"  # Exemple de chemin pour sauvegarder la vidéo
my_camera = planktoscope.imagernew.picamera.picamera(output=output_path)

# Créer et démarrer le thread
camera_thread = CameraThread(camera=my_camera, command_queue=command_queue, stop_event=stop_event)
camera_thread.start()

# Interagir avec la caméra via le thread
# Par exemple, pour capturer une image :
command_queue.put(("capture", [], {"path": "/path/to/save/image.jpg"}))

# Lorsque vous avez terminé avec la caméra et que vous voulez arrêter le thread :
stop_event.set()  # Signale au thread de s'arrêter
camera_thread.join()  # Attendre que le thread se termine proprement