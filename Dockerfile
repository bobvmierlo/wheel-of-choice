# Wheel of Choice — container image.
#
#   docker build -t wheel-of-choice .
#   docker run -p 8000:8000 -v wheel-data:/data wheel-of-choice
#
# Data (accounts, wheels, history, VAPID key) lives in the /data volume,
# so it survives `docker rm` and image upgrades.
FROM python:3.12-slim

WORKDIR /app

# Install every dependency — including the optional ones for push
# notifications and calendar busy/free — so the whole app works out of
# the box. Wheels for these exist on PyPI, so no build toolchain is needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Wheels & history are stored here; mount a volume to keep them.
ENV WHEEL_DATA_DIR=/data
VOLUME ["/data"]

# Run as an unprivileged user that owns the data directory.
RUN useradd --system --uid 10001 wheel \
 && mkdir -p /data \
 && chown -R wheel:wheel /data
USER wheel

# Match the bare-metal defaults; override at run time if you like.
# WHEEL_DEPLOYMENT tells the app it's containerised, so the admin panel shows
# "pull a new image to update" instead of the git-pull button.
ENV HOST=0.0.0.0 \
    PORT=8000 \
    WHEEL_DEPLOYMENT=docker
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD \
    python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/')" \
    || exit 1

CMD ["python", "server.py"]
