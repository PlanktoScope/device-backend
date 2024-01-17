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
  export PATH="/home/pi/.local/bin:$PATH" && \
  pip install --no-cache-dir poetry==1.7.1 --extra-index-url https://www.piwheels.org/simple && \
  poetry install --no-root --only main --compile && \
  poetry --no-interaction cache list && \
  poetry --no-interaction cache clear pypi --all && \
  poetry --no-interaction cache clear piwheels --all && \
  rm -rf /home/pi/.cache/pypoetry/artifacts && \
  rm -rf /home/pi/.cache/pypoetry/cache && \
  pip cache purge && \
  rm -rf /home/pi/.cache/pip

# Set up application

COPY main.py .
COPY planktoscope/ ./planktoscope
ENTRYPOINT ["/home/pi/.local/bin/poetry", "run", "python", "main.py"]
EXPOSE 8001
VOLUME /home/pi/device-backend-logs/processing/segmenter
VOLUME /home/pi/data
