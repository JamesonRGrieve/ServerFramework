FROM python:3.10.16-bullseye

ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=UTF-8 \
    PLAYWRIGHT_BROWSERS_PATH=0 \
    HNSWLIB_NO_NATIVE=1 \
    LD_PRELOAD=libgomp.so.1 \
    LD_LIBRARY_PATH=/usr/local/lib64/: \
    DEBIAN_FRONTEND=noninteractive \
    CHROME_BIN=/usr/bin/chromium \
    CHROMIUM_PATH=/usr/bin/chromium \
    CHROMIUM_FLAGS=--no-sandbox

# Install OS packages in a single layer
RUN set -eux && \
    apt-get update && \
    apt-get upgrade -y && \
    curl -sL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends \
    build-essential \
    bzip2 \
    curl \
    default-libmysqlclient-dev \
    ffmpeg \
    file \
    imagemagick \
    libasound-dev \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libatspi2.0-0 \
    libbluetooth-dev \
    libbz2-dev \
    libcups2 \
    libcurl4-openssl-dev \
    libffi-dev \
    libglib2.0-dev \
    libgmp-dev \
    libgomp1 \
    libjpeg-dev \
    libkrb5-dev \
    liblzma-dev \
    libmagickcore-dev \
    libmagickwand-dev \
    libmaxminddb-dev \
    libncurses5-dev \
    libncursesw5-dev \
    libnspr4 \
    libnss3 \
    libpng-dev \
    libportaudio2 \
    libpq-dev \
    libreadline-dev \
    libsqlite3-dev \
    libssl-dev \
    libwebp-dev \
    libxcomposite1 \
    libxml2-dev \
    libxslt-dev \
    libyaml-dev \
    libreoffice \
    nodejs \
    openssh-client \
    poppler-utils \
    procps \
    sqlite3 \
    tk-dev \
    unixodbc \
    unixodbc-dev \
    unoconv \
    unzip \
    uuid-dev \
    wget \
    xvfb \
    xz-utils \
    zlib1g-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /server

# Install Python dependencies first
COPY requirements.txt /server/requirements.txt

# Pre-create the .venv that app.py expects
RUN python3 -m venv /server/.venv && \
    /server/.venv/bin/python -m pip install --upgrade pip && \
    /server/.venv/bin/python -m pip install -r /server/requirements.txt

# Copy application code
COPY . /server

EXPOSE 1996

# Activate venv by prepending to PATH so subprocesses (alembic) can find venv binaries
ENV PATH="/server/.venv/bin:$PATH"

# Run with venv python (PATH ensures alembic and other tools are found)
ENTRYPOINT ["python3", "src/app.py"]
