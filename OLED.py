import board
import digitalio
import busio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306

# Définir l'adresse I2C de l'écran OLED (0x3D)
I2C_ADDRESS = 0x3d

# Initialisation du bus I2C
i2c = busio.I2C(board.SCL, board.SDA)

# Initialisation de l'écran OLED
oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c, addr=I2C_ADDRESS)

# Effacer l'écran
oled.fill(0)
oled.show()

# Créer une image vierge avec mode "1" pour 1-bit color (noir et blanc)
image = Image.new("1", (oled.width, oled.height))

# Créer un objet ImageDraw pour dessiner sur l'image
draw = ImageDraw.Draw(image)

# Charger une police par défaut
font = ImageFont.load_default()

# Texte à afficher
text = "Fairscope"

# Calculer la taille du texte
bbox = draw.textbbox((0, 0), text, font=font)
text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]

# Calculer les positions x et y pour centrer le texte
x = (oled.width - text_width) // 2
y = (oled.height - text_height) // 2

# Dessiner le texte au centre de l'image
draw.text((x, y), text, font=font, fill=255)

# Afficher l'image sur l'écran OLED
oled.image(image)
oled.show()
