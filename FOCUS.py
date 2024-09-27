import spidev
from gpiozero import DigitalOutputDevice
from time import sleep
from time import perf_counter_ns
import time

# Configuration SPI
spi = spidev.SpiDev()
spi.open(0, 1) #1 Stepper #0 Pump Ouvre le bus SPI (0) et le périphérique (0)
spi.max_speed_hz = 1000000 # Définit la vitesse SPI à 1 MHz

# Configuration des broches GPIO pour Step, Dir et GPIO5
step_pin = 13  #13 Stepper #12 Pump  Numéro de broche pour le signal Step
dir_pin = 17   #17 Stepper #27 Pump  Numéro de broche pour le signal Dir
gpio5_pin = 5  #5 Stepper #23 Pump  Numéro de broche GPIO5 pour le contrôle du moteur

# Initialise les objets contrôlant les broches GPIO
step_motor = DigitalOutputDevice(step_pin)
dir_motor = DigitalOutputDevice(dir_pin)
gpio5 = DigitalOutputDevice(gpio5_pin)

# Fonction pour envoyer des commandes au TMC5160 via SPI
def write_register(address, value):
    spi.xfer2([address | 0x80] + [(value >> i) & 0xFF for i in (24, 16, 8, 0)])

# Initialisation des registres du TMC5160
def setup_tmc5160():
    # Configuration générale
    write_register(0x00, 0x00000008)  # GCONF: disable internal oscillator, enable single wire operation
    
    # Configuration du courant de maintien et de marche
    write_register(0x10, 0x00080501)  # Configuration du courant

    # Configuration du hachage (chopper)
    write_register(0x6C, 0x000100C3)  # CHOPCONF: Configuration du hachage

    # Configuration PWM
    write_register(0x70, 0xC40C001E)  # PWMCONF: Configuration PWM

    # Mode de vitesse constante
    write_register(0x20, 0x00000002)  # RAMPMODE: Mode de vitesse constante

    # Configuration des vitesses
    write_register(0x23, 1)       # VSTART
    write_register(0x24, 2000)    # A1
    write_register(0x25, 3000)    # V1
    write_register(0x26, 5000)    # AMAX
    write_register(0x27, 100000)  # VMAX
    write_register(0x28, 5000)    # DMAX
    write_register(0x2A, 4000)    # D1
    write_register(0x2B, 10)      # VSTOP

# Initialise le TMC5160
setup_tmc5160()

# Fonction pour démarrer le moteur
def start_motor():
    gpio5.off()  # Met GPIO5 à HIGH pour démarrer le moteur
    write_register(0x27, 0x000F4240)  # Définit la vitesse maximale à 1 000 000

# Fonction pour arrêter le moteur
def stop_motor():
    gpio5.on()  # Met GPIO5 à LOW pour arrêter le moteur
    write_register(0x27, 0x00000000)  # Arrête le moteur en définissant la vitesse à 0

# Fonction pour générer le signal DIR avec une fréquence optimisée
def generate_dir_signal():
    step_motor.on()   # Active le signal Step
    start_time = time.time_ns()
    while time.time_ns() - start_time < 1:
        pass
    step_motor.off()  # Désactive le signal Step

# Fonction pour déplacer le moteur pas à pas avec des micro-pas
def move_motor_step(direction, steps):
    microsteps = steps * 256  # Convertit les pas en micro-pas
    move_motor(direction, microsteps)  # Déplace le moteur en fonction du nombre total de micro-pas

# Fonction pour déplacer le moteur pas à pas
def move_motor(direction, microsteps):
    # Contrôle de la direction du moteur
    if direction == "forward":
        dir_motor.on()    # Active la direction vers l'avant
    elif direction == "backward":
        dir_motor.off()   # Active la direction vers l'arrière
    
    # Envoi des impulsions pour le nombre de pas spécifié
    for _ in range(microsteps):
        generate_dir_signal()

try:
    gpio5.off()  # Assure que le moteur est arrêté au début
    stop_motor()  # Arrête le moteur pour garantir qu'il ne tourne pas au démarrage
    start_motor()  # Démarre le moteur
    move_motor_step("backward", 1)  # Déplace le moteur de 5 pas vers l'avant
    # stop_motor()  # Décommente cette ligne pour arrêter le moteur après le mouvement
except KeyboardInterrupt:
    print("Arrêt du programme")
finally:
    stop_motor()  # S'assure que le moteur est arrêté
    spi.close()   # Ferme la connexion SPI
