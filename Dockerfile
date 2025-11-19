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
    python3 \
    python3-venv \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 2. SZYBKA WERYFIKACJA: Sprawdź czy czcionka się zainstalowała.
# Jeśli ten krok zwróci błąd, będziemy wiedzieć, że apt zawiódł.
RUN ls -lh /usr/share/fonts/truetype/noto/NotoSerif-Bold.ttf && echo ">>> CZCIONKA ZAINSTALOWANA POPRAWNIE <<<"

# 3. Logika warunkowa dla Raspberry Pi (kamera)
RUN if [ "$TARGETARCH" = "arm64" || "$TARGETARCH" = "arm32" ]; then \
        echo ">>> Wykryto Raspberry Pi (arm64). Instalacja bibliotek kamery..." && \
        curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg && \
        echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] https://archive.raspberrypi.com/debian/ bookworm main" \
        > /etc/apt/sources.list.d/raspi.list && \
        apt-get update && \
        apt-get install -y --no-install-recommends \
            libcamera-apps \
            python3-picamera2 \
        && apt-get clean && rm -rf /var/lib/apt/lists/*; \
    else \
        echo ">>> Wykryto architekturę $TARGETARCH (PC/Laptop). Pomijanie instalacji kamery."; \
    fi

# 4. Wirtualne środowisko i pakiety Python
RUN python3 -m venv --system-site-packages $VIRTUAL_ENV \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir websockets websocket-client \
    && pip install --no-cache-dir google-generativeai google-api-core Pillow numpy

# 5. Odświeżenie cache fontów
RUN fc-cache -fv

WORKDIR /app
COPY capture.py /app/capture.py

CMD ["python3", "/app/capture.py"]