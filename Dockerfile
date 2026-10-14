# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
FROM docker:29.1-cli
RUN apk add --no-cache curl bash openssl make openssh build-base sudo shadow shadow-login
# Create a non-root user
ARG USERNAME=appuser
ARG HOME=/home/$USERNAME
ARG USER_UID=10000
ARG USER_GID=10000

# Add the non-root user to the system
RUN addgroup -g $USER_GID $USERNAME \
    && adduser -S -u $USER_UID -G $USERNAME $USERNAME \
    && echo "$USERNAME ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME \
    && mkdir -p $HOME && chown -R $USERNAME:$USERNAME $HOME \
    && mkdir -p /app/framework && chown -R $USERNAME:$USERNAME /app

# Add the non-root user to the docker group
RUN usermod -aG docker $USERNAME

WORKDIR /app

# Installkubectl
RUN curl -LO https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl && \
    install -m 755 kubectl /usr/local/bin/kubectl && \
    rm kubectl

# Install k3d
RUN curl -Lo /usr/local/bin/k3d https://github.com/k3d-io/k3d/releases/download/v5.8.3/k3d-linux-amd64 && \
    chmod +x /usr/local/bin/k3d

# Install helm (v4.1.0)
RUN curl -Lo /tmp/helm-v4.1.0-linux-amd64.tar.gz https://get.helm.sh/helm-v4.1.0-linux-amd64.tar.gz && \
    tar -xzf /tmp/helm-v4.1.0-linux-amd64.tar.gz -C /tmp && \
    mv /tmp/linux-amd64/helm /usr/local/bin/helm && \
    chmod +x /usr/local/bin/helm && \
    rm -rf /tmp/helm-v4.1.0-linux-amd64.tar.gz /tmp/linux-amd64

# Switch to the non-root user
USER $USERNAME

# uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/${HOME}/.local/bin:/app/.venv/bin:${PATH}"

# python: framework is installed into .venv via uv sync (editable workspace install;
# changes under src/production_test_framework/ are picked up without rebuilding the image).
RUN uv python install 3.14

# Copy framework (current context = repo root)
COPY --chown=$USERNAME:$USERNAME . ./framework/

# Setup an ansible.cfg file to use the SSH agent for SSH connections
RUN <<EOF
echo "[ssh_connection]" > /home/${USERNAME}/.ansible.cfg
echo "ssh_args = -o ForwardAgent=yes -o ControlMaster=auto -o ControlPersist=60s -o ControlPath=/tmp/ansible-ssh-%h-%p-%r -o StrictHostKeyChecking=accept-new" >> /home/${USERNAME}/.ansible.cfg
EOF

WORKDIR /app/framework

# Optional: set at image build time when .git is unavailable (e.g. trimmed context).
# Tag releases pass SETUPTOOLS_SCM_PRETEND_VERSION from CI (see .github/workflows/docker.yml).
ARG SETUPTOOLS_SCM_PRETEND_VERSION=
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

# Install all of the required python dependencies
RUN uv venv --clear && uv sync --all-packages && uv cache clean

# create a .env file in the framework directory
RUN <<EOF
echo "ANSIBLE_HOME=${ANSIBLE_HOME:-/app/framework/ansible}" > .env
echo "ANSIBLE_INVENTORY_FILE=${ANSIBLE_INVENTORY_FILE:-/app/framework/ansible/inventory.ini}" >> .env
echo "REMOTE_HOST=${REMOTE_HOST:-localhost}" >> .env
echo "CLUSTER=${CLUSTER:-production-test-cluster}" >> .env
echo "REMOTE_USER=${REMOTE_USER:-appuser}" >> .env
EOF

# Create the results, tests, and charts directories
RUN mkdir -p /app/results /app/framework/tests /app/framework/charts

CMD ["/app/framework/scripts/docker-entrypoint.sh"]

ARG GIT_HASH=unknown
LABEL org.opencontainers.image.revision="${GIT_HASH}"
