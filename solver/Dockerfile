FROM nrel/openstudio:3.9.0
ARG OPENSTUDIO_VERSION=3.9.0

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LANGUAGE=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV GEM_HOME=/usr/local/lib/ruby/gems/3.2.2
ENV GEM_PATH=/usr/local/lib/ruby/gems/3.2.2
# Add gem bin directory to PATH so uo command is accessible
ENV PATH="/usr/local/lib/ruby/gems/3.2.2/bin:/usr/local/bin:${PATH}"

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

# Install urbanopt-cli and verify installation
RUN gem install urbanopt-cli -v 1.0.1 && \
    echo "Gem installation complete. Checking gem bin directory:" && \
    ls -la /usr/local/lib/ruby/gems/3.2.2/bin/ || echo "Gem bin directory not found" && \
    echo "Testing uo command accessibility:" && \
    /usr/local/lib/ruby/gems/3.2.2/bin/uo --version && \
    echo "uo command verified successfully"

# Pre-warm urbanopt's runtime gems for both commercial AND residential paths
RUN rm -f /usr/local/bin/bundle /usr/local/bin/bundler && \
    gem install --no-document bundler -v 2.4.10 && \
    uo create --combined --project-folder /tmp/prewarm && \
    cd /tmp/prewarm && \
    bundle install --jobs 4 --retry 3 && \
    gem install ruby2_keywords -v 0.0.5 && \
    rm -rf /tmp/prewarm

# Final verification that uo is in PATH
RUN which uo && uo --version
# Copy application code and set environment variables
COPY . .

# Install the solver package
RUN pip install -e ./app

RUN chmod +x /solver/entrypoint.sh
ENTRYPOINT ["/solver/entrypoint.sh"]

# Expose the required port
EXPOSE 8080

# Set the default command
CMD ["python3", "run.py"]