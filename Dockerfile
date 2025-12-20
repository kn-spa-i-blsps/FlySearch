# Bazowy obraz: Debian Bookworm
FROM debian:bookworm

ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# 1. Instalacja pakietów WSPÓLNYCH (Python, Fonty, Narzędzia)
# Łączymy update i install w jednej linii dla pewności.
# Pakiet 'fonts-noto-serif' zawiera plik NotoSerif-Bold.ttf w /usr/share/fonts/truetype/noto/
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gpg \
    ca-certificates \
    fontconfig \
    fonts-noto-core \
    build-essential \
    python3 \
    python3-dev \
    libjpeg-dev \
    zlib1g-dev \
    python3-venv \
    python3-pip \
    fswebcam \
    && rm -rf /var/lib/apt/lists/*

RUN case "$(dpkg --print-architecture)" in \
  arm64|aarch64|armhf|armv7l|arm32) \
    . /etc/os-release; \
    curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
      | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] https://archive.raspberrypi.com/debian/ ${VERSION_CODENAME} main" \
      > /etc/apt/sources.list.d/raspi.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends libcamera-apps python3-picamera2 python3-libcamera libcamera-ipa \
    && apt-get clean && rm -rf /var/lib/apt/lists/* ; \
    ;; \
  *) echo "Non-ARM – skipping Picamera2 install"; ;; \
esac



# Venv and Python packagres
RUN python3 -m venv --system-site-packages $VIRTUAL_ENV \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir websockets websocket-client \
    && pip install --no-cache-dir google-generativeai google-api-core Pillow numpy pymavlink pyserial

# Refreshing cache fonts
RUN fc-cache -fv

WORKDIR /app
COPY . /app