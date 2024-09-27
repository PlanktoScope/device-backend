import smbus2 as smbus
import time

# Adresse du périphérique I2C
DEVICE_ADDRESS = 0x64

# Registres du LM36011YKBR
REGISTER_ENABLE = 0x01
REGISTER_TORCH = 0x04
REGISTER_FLASH = 0x03

def i2c_write_byte(register, data):
    """Écrit un octet dans un registre via I2C."""
    with smbus.SMBus(1) as bus:
        bus.write_byte_data(DEVICE_ADDRESS, register, data)

def set_torch_current(current_mA):
    """Définit le courant de torche LED en milliampères (mA)."""
    # Conversion du courant en valeur de registre
    # Ajuste cette conversion selon la spécification exacte du LM36011YKBR
    value = int(current_mA * 0.34)  # Conversion approximative
    print(f"Définition du courant de torche à {current_mA}mA (valeur de registre {value})")
    i2c_write_byte(REGISTER_TORCH, value)

def set_flash_current(current_mA):
    """Définit le courant de flash LED en milliampères (mA)."""
    # Conversion du courant en valeur de registre
    # Ajuste cette conversion selon la spécification exacte du LM36011YKBR
    value = int(current_mA * 0.085)  # Conversion approximative
    print(f"Définition du courant de flash à {current_mA}mA (valeur de registre {value})")
    i2c_write_byte(REGISTER_FLASH, value)

def activate_torch():
    """Active la torche LED."""
    print("Activation de la torche")
    i2c_write_byte(REGISTER_ENABLE, 0b10)  # Activer la torche LED

def deactivate_torch():
    """Désactive la torche LED."""
    print("Désactivation de la torche")
    i2c_write_byte(REGISTER_ENABLE, 0b00)  # Désactiver la torche LED

if __name__ == "__main__":
    try:
        # Définir le courant de torche et de flash à 20mA
        set_torch_current(10)  # Courant de torche à 20mA
        set_flash_current(10)  # Courant de flash à 20mA, si nécessaire
        
        # Allumer la LED
        activate_torch()
        time.sleep(10)  # Laisser la LED allumée pendant 5 secondes

        # Éteindre la LED
        deactivate_torch()
    finally:
        print("Script terminé")
