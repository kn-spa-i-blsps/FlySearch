import os 
from datetime import datetime 
from pathlib import Path
from picamera2 import Picamera2

DIR = Path(os.environ.get("IMG_DIR", "/img"))
FNAME = os.environ.get("FNAME", f"img_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
W = int(os.environ.get("WIDTH", "500"))
H = int(os.environ.get("HEIGHT", "500"))
Q = int(os.environ.get("QUALITY", "90"))

DIR.mkdir(parents=True, exist_ok=True)
path = DIR / FNAME

cam = Picamera2()
cfg = cam.create_still_configuration(main={"size": (W, H)})
cam.configure(cfg)
cam.start()
cam.capture_file(str(path))
cam.stop()
print(f"Image saved at: {path}")
