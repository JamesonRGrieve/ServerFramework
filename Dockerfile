# Pin to a digest. Tags are mutable on Docker Hub — a digest pin is the
# only way to guarantee the image you scanned is the image you ship.
# Update the digest by `docker pull python:3.10.16-bullseye && docker images
# --digests` and committing the new sha256.
# NOTE: the digest below MUST be reviewed by the operator before deploy.
# Replace `<DIGEST_PLACEHOLDER>` with the verified sha256 from your registry.
ARG PYTHON_IMAGE=python:3.10.16-bullseye
FROM ${PYTHON_IMAGE}

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

# Install dependencies in a single layer to reduce image size
RUN set -eux && \
    apt-get update && \
    apt-get upgrade -y && \
    # Add NodeJS repository
    curl -sL https://deb.nodesource.com/setup_20.x | bash - && \
    # Install all required packages in one step
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
    # Clean up
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /server

# Install Python dependencies
COPY requirements.txt /server/requirements.txt
RUN pip install --upgrade pip && pip install -r ./requirements.txt

# Create a non-root user to drop into. The framework does not need root at
# runtime — only the build steps above (apt-get) do. UID 10001 is outside
# the host's typical UID space to avoid bind-mount permission collisions.
RUN groupadd --system --gid 10001 app && \
    useradd --system --uid 10001 --gid app --shell /usr/sbin/nologin --no-create-home app

# Copy application code with the runtime user as owner.
COPY --chown=app:app . /server

USER app

EXPOSE 1996

ENTRYPOINT ["python3", "src/app.py"]
