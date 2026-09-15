FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# opencv needs libGL/glib even in "headless" builds; ffmpeg backs cv2's
# RTSP decode (cv2.CAP_FFMPEG)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only torch explicitly first - pip's default PyPI wheel pulls
# in CUDA/cuDNN (several GB) unconditionally even on this ARM board with
# no NVIDIA GPU. Installing from the CPU index first means the later
# `pip install -r requirements.txt` sees torch already satisfied and
# skips pulling a CUDA build in as a transitive dep of ultralytics/torchreid.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY camera/ ./camera/
COPY detection/ ./detection/
COPY DataClass/ ./DataClass/
COPY tracking/ ./tracking/
COPY embedding/ ./embedding/
COPY streaming/ ./streaming/
COPY yolov8n.pt .

# Bake the NCNN export in at build time (ARM-optimized inference format -
# see streaming/export_ncnn.py) so it's not redone on every container
# start. imgsz must match YOLO_IMGSZ in .env.
ARG YOLO_EXPORT_IMGSZ=320
RUN python -m streaming.export_ncnn ${YOLO_EXPORT_IMGSZ}

CMD ["python", "-m", "streaming.capture_node"]
