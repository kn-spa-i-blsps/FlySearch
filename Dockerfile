# Base image: Debian Bookworm
FROM debian:bookworm

ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Installation of COMMON packages (Python, fonts, tools)
# We combine update and install in a single line to be safe.
# The 'fonts-noto-serif' package includes the file NotoSerif-Bold.ttf in /usr/share/fonts/truetype/noto/
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

# Raspberry Pi camera stack (libcamera / Picamera2) comes from the Raspberry Pi APT repo and is only available on ARM.
# Install it conditionally so the same Dockerfile can build on x86/amd64 (where we skip these packages).
RUN if [ "${TARGETARCH}" = "arm64" ] || [ "${TARGETARCH}" = "arm" ]; then \
      . /etc/os-release; \
      curl -fsSL https://archive.raspberrypi.com/debian/raspberrypi.gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/raspberrypi-archive-keyring.gpg; \
      echo "deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.gpg] https://archive.raspberrypi.com/debian/ ${VERSION_CODENAME} main" \
        > /etc/apt/sources.list.d/raspi.list; \
      apt-get update; \
      apt-get install -y --no-install-recommends \
        libcamera-apps \
        python3-picamera2 \
        python3-libcamera \
        libcamera-ipa; \
      rm -rf /var/lib/apt/lists/*; \
    else \
      echo "Non-ARM (${TARGETARCH}) - skipping Picamera2/libcamera install"; \
    fi

WORKDIR /app

# Install Python deps in a cache-friendly way
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv --system-site-packages "${VIRTUAL_ENV}" \
  && pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r /app/requirements.txt

# Refresh font cache (optional, but useful if you render text)
RUN fc-cache -fv

# Copy the rest of the project
COPY . /app