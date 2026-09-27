"""
Certificate verification helpers.

Uploaded documents are stored as PDFs (see app/api/routes/student.py), but QR
detection and image heuristics work on raster images — so every function here
transparently rasterizes the PDF's first page to an image before analyzing it.
You don't need to change how documents are uploaded; this just adapts to what's
actually on disk.

QR detection uses OpenCV's built-in QRCodeDetector as the primary method (it
ships fully self-contained with opencv-python, no external system libraries
required — unlike pyzbar, whose bundled zbar DLL can fail to load on some
Windows machines due to a missing libiconv.dll dependency). pyzbar is kept as
an optional secondary attempt for cases OpenCV might miss, but is no longer
required for QR detection to work.

To make OpenCV's detector as reliable as possible without extra dependencies,
detection is attempted against a few different preprocessed versions of the
page image (original, grayscale, contrast-enhanced, upscaled) since real-world
QR codes vary a lot in size, contrast, and placement on a certificate.

All the heavy libraries (PyMuPDF, OpenCV, pyzbar) are imported defensively. If
one isn't installed or fails to load on a given machine, verification degrades
gracefully to "unavailable" signals instead of crashing the request — the same
resilience pattern used for email sending elsewhere in this backend.
"""

import os
import re
from urllib.parse import urlparse, parse_qs
from typing import Optional

# --- Defensive imports: verification degrades gracefully if a lib is missing ---
try:
    import pymupdf as fitz  # PyMuPDF — used to rasterize PDF pages to images
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False

try:
    from pyzbar.pyzbar import decode as zbar_decode
    HAS_PYZBAR = True
except Exception:
    # pyzbar can fail in a few different ways depending on the machine —
    # a clean ImportError if the package itself is missing, or (on some
    # Windows systems) a FileNotFoundError when its bundled zbar DLL can't
    # load because a dependency like libiconv.dll isn't present. Catching
    # broadly here means either failure degrades gracefully rather than
    # crashing the whole server on startup. OpenCV's detector (below) is the
    # primary QR method now, so this no longer blocks verification entirely.
    HAS_PYZBAR = False

try:
    from PIL import Image, ExifTags
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    import easyocr
    HAS_EASYOCR = True
except Exception:
    # EasyOCR pulls in
