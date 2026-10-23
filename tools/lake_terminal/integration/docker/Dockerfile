FROM python:3.13-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash curl git iproute2 lsof procps tmux zsh \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY tools/lake_terminal /opt/lake-terminal
RUN python -m pip install --no-cache-dir /opt/lake-terminal

CMD ["lake", "--test-integration", "synthetic", "all"]
