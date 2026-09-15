"""
One-time export of yolov8n.pt to NCNN format, which runs meaningfully
faster than plain PyTorch on ARM CPUs (Raspberry Pi-class boards, so the
Pi5 here) - this is Ultralytics' own recommended format for that hardware.

Run once wherever the model will actually be used for inference (the
exported files are imgsz- and platform-specific):
    python -m streaming.export_ncnn [imgsz]

Produces a yolov8n_ncnn_model/ directory next to yolov8n.pt - point
YOLO_MODEL at that directory (not a .pt file) to use it.
"""

import sys

from ultralytics import YOLO

imgsz = int(sys.argv[1]) if len(sys.argv) > 1 else 320

print(f"Exporting yolov8n.pt to NCNN (imgsz={imgsz})...")
model = YOLO("yolov8n.pt")
exported_path = model.export(format="ncnn", imgsz=imgsz)
print(f"Done: {exported_path}")
print("Set YOLO_MODEL to that path in .env to use it.")
