FROM python:3.9.18-slim-bullseye

# Install dependencies

COPY apt-packages /tmp/
RUN \
  apt-get update && \
  apt-get -y upgrade && \
  grep -v '^#' /tmp/apt-packages | xargs apt-get -y install --no-install-recommends && \
  apt-get clean && \
  rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/
# Note: cmake is needed to install ninja which is needed to install pyproject.toml-based projects
RUN \
  apt-get update && \
  apt-get -y install cmake && \
  pip install --no-cache-dir -r /tmp/requirements.txt && \
  apt-get -y purge cmake && \
  apt-get -y autoremove && \
  apt-get clean && \
  rm -rf /var/lib/apt/lists/*

# Set up application

RUN useradd --create-home pi
WORKDIR /home/pi/device-backend/processing/segmenter
USER pi
COPY planktoscope/ main.py .
ENTRYPOINT ["python", "./main.py"]
