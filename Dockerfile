# Bazowy obraz: Debian Bookworm (kompatybilny z Raspberry Pi OS)
FROM debian:bookworm

# Ustaw zmienne środowiskowe (np. nieinteraktywny tryb apt)
ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Zainstaluj podstawowe narzędzia i zależności systemowe
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gpg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Dodaj repozytorium Raspberry Pi i jego klucz GPG
RUN curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
    | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] https://archive.raspberrypi.com/debian/ bookworm main" \
    > /etc/apt/sources.list.d/raspi.list

# Zainstaluj pakiety RPi, Python oraz Picamera2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcamera-apps \
    fontconfig \
    fonts-noto-serif \
    python3 \
    python3-venv \
    python3-pip \
    python3-picamera2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Utwórz i aktywuj wirtualne środowisko Pythona
RUN python3 -m venv --system-site-packages $VIRTUAL_ENV \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir websockets websocket-client \
    && pip install --no-cache-dir google-generativeai google-api-core Pillow

# Opcjonalnie: odświeżenie cache fontów (przydatne, jeśli używasz matplotlib itp.)
RUN fc-cache -fv

# Ustaw katalog roboczy
WORKDIR /app

# Skopiuj plik aplikacji
COPY capture.py /app/capture.py

# Domyślne polecenie uruchamiające kontener
CMD ["python3", "/app/capture.py"]
