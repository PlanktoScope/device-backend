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
RUN \
  pip install --no-cache-dir -r /tmp/requirements.txt && \

# Set up application

RUN useradd --create-home pi
WORKDIR /home/pi/device-backend/processing/segmenter
USER pi
COPY planktoscope/ main.py .
ENTRYPOINT ["python", "./main.py"]
