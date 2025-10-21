import os 
from datetime import datetime 
from pathlib import Path
from picamera2 import Picamera2

DIR = Path(os.environ.get("OUT_DIR", "./out")) # save files to /out in the container
H = 1080
W = 1920
os.makedirs(DIR, exist_ok=True)
file_name = f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
path = os.path.join(DIR, file_name)

camera = Picamera2()
camera_config = camera.create_still_configuration(main={"size": (W, H)})
camera.configure(camera_config)
camera.start()
camera.capture_file(path)
camera.stop()
print(f"Image saved at: {path}")
