# to be built on RPi
FROM debian:bookworm 

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates udev libcamera-apps python3 python3-pip python3-picamera2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir websockets

WORKDIR /app

COPY capture.py /app/capture.py

CMD ["python3", "/app/capture.py"]