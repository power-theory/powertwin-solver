FROM nrel/openstudio:3.9.0
ARG OPENSTUDIO_VERSION=3.9.0

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LANGUAGE=C.UTF-8
ENV LC_ALL=C.UTF-8
# Use base image's default GEM paths - don't override

WORKDIR /solver

# Install system dependencies, including libffi and libyaml for Ruby
RUN apt-get update && apt-get -y upgrade && apt-get install -y \
  software-properties-common \
  ca-certificates \
  emacs \
  git \
  locales \
  locales-all \
  sudo \
  libpq-dev \
  mpich \
  libmpich-dev

# Add deadsnakes PPA for Python 3.10 and install Python 3.10
RUN add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.10 python3.10-dev python3.10-distutils

# Set Python 3.10 as the default Python version
RUN ln -sf /usr/bin/python3.10 /usr/bin/python && \
    ln -sf /usr/bin/python3.10 /usr/bin/python3

# Upgrade pip and install Python dependencies
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python
COPY ./requirements.txt /solver/requirements.txt
RUN pip install --ignore-installed -r requirements.txt

# Install Ruby dependencies
RUN gem install public_suffix -v 5.1.1
RUN gem uninstall multipart-post --force --all && \
    gem install multipart-post -v 2.1.1

# Install ruby2_keywords and verify immediately
RUN gem install ruby2_keywords -v 0.0.5 && \
    gem list ruby2_keywords && \
    gem which ruby2_keywords

# Install urbanopt-cli and verify installation
RUN gem install urbanopt-cli -v 1.1.0 && \
    echo "Gem installation complete. Checking gem environment:" && \
    gem env && \
    echo "Finding uo command:" && \
    which uo && \
    uo --version && \
    echo "uo command verified successfully"

# Install OpenStudio extension gems required by PowerTwin.rb
# Versions pinned for compatibility with urbanopt-cli 1.1.0 / openstudio-extension 0.9.4
RUN gem install openstudio-common-measures -v 0.12.3 && \
    gem install openstudio-model-articulation -v 0.12.1 && \
    gem install openstudio-ee -v 0.12.3 && \
    gem install openstudio-calibration -v 0.12.1 && \
    gem install openstudio-load-flexibility-measures -v 0.11.1 && \
    gem install openstudio-geb -v 0.7.0

# Final verification
RUN echo "=== INSTALLED GEMS ===" && \
    gem list | grep -E "ruby2_keywords|urbanopt|openstudio" && \
    echo "=== UO VERIFICATION ===" && \
    which uo && uo --version
# Copy application code and set environment variables
COPY . .

# Install the solver package
RUN pip install -e ./app

# Expose the required port
EXPOSE 8080

# Set the default command
CMD ["python3", "run.py"]
