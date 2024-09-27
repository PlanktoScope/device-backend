from gpiozero import PWMOutputDevice
from time import sleep

# Définir le numéro de la broche GPIO à utiliser pour le signal PWM
pwm_pin = 18

# Définir la fréquence du signal PWM (en Hz)
frequency = 0.25

# Initialiser la sortie PWM sur la broche GPIO spécifiée
pwm = PWMOutputDevice(pwm_pin, frequency=frequency)

try:
    # Démarrer le signal PWM avec un cycle de travail de 0.5 (50 %)
    pwm.value = 0.5


    # Faire clignoter le signal PWM à 50 % de cycle de travail pendant 10 secondes
    sleep(100)

finally:
    # Arrêter le signal PWM
    pwm.close()
