FROM nrel/openstudio:3.7.0
ARG OPENSTUDIO_VERSION=3.7.0

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LANGUAGE=C.UTF-8
ENV LC_ALL=C.UTF-8

WORKDIR /app

# Install system dependencies, including libffi and libyaml for Ruby
RUN apt-get update && apt-get -y upgrade && apt-get install -y \
  software-properties-common \
  ca-certificates \
  emacs \
  git \
  locales \
  locales-all \
  sudo \
  libpq-dev


# Add deadsnakes PPA for Python 3.10 and install Python 3.10
RUN add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.10 python3.10-dev python3.10-distutils

# Set Python 3.10 as the default Python version
RUN ln -sf /usr/bin/python3.10 /usr/bin/python && \
    ln -sf /usr/bin/python3.10 /usr/bin/python3

# Upgrade pip and install Python dependencies
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python
COPY ./requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Ruby dependencies
RUN gem install rchardet -v 1.8.0
RUN gem install public_suffix -v 5.1.1
RUN gem install urbanopt-cli -v 0.13

# Copy application code and set environment variables
COPY . .

# Expose the required port
EXPOSE 8080

# Set the default command
#CMD ["bash"]
CMD ["python3", "app/app.py"]
