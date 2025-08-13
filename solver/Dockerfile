FROM nrel/openstudio:3.7.0
ARG OPENSTUDIO_VERSION=3.7.0

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LANGUAGE=C.UTF-8
ENV LC_ALL=C.UTF-8

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
RUN pip install --no-cache-dir -r requirements.txt

# Install Ruby dependencies
RUN gem install rchardet -v 1.8.0
RUN gem install public_suffix -v 5.1.1

RUN gem uninstall multipart-post --force --all && \
    gem install multipart-post -v 2.1.1

RUN gem install urbanopt-cli -v 0.13

# Copy application code and set environment variables
COPY . .

# Install the solver package
RUN pip install -e ./app

# Expose the required port
EXPOSE 8080

# Set the default command
CMD ["python3", "run.py"]
