FROM python:3.9.18-slim-bullseye

# Install OS dependencies

COPY apt-packages /tmp/
RUN \
  apt-get update && \
  apt-get -y upgrade && \
  grep -v '^#' /tmp/apt-packages | xargs apt-get -y install --no-install-recommends && \
  apt-get clean && \
  rm -rf /var/lib/apt/lists/* && \
  rm /tmp/apt-packages

# Drop root user

RUN useradd --create-home pi
WORKDIR /home/pi/device-backend/processing/segmenter
USER pi

# Install Python dependencies

# Note: cmake is needed to install ninja which is needed to install pyproject.toml-based projects.
# We must remove it in the same RUN command as where we installed it, if we want to keep it out of
# the container image.
COPY pyproject.toml poetry.lock .
RUN \
  apt-get update && \
  apt-get -y install cmake && \
  pip install poetry==1.7.1 && \
  poetry install --no-root --no-dev --compile && \
  apt-get -y purge cmake && \
  apt-get -y autoremove && \
  apt-get clean && \
  rm -rf /var/lib/apt/lists/*

# Set up application

COPY planktoscope/ main.py .
ENTRYPOINT ["python", "./main.py"]
