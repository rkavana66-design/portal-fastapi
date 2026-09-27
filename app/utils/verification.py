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
    # EasyOCR pulls in PyTorch, which is a meaningfully large dependency —
    # if it's not installed (or fails to load for any reason, e.g. low
    # memory), OCR-based verification degrades gracefully to "unavailable"
    # rather than crashing document scanning entirely.
    HAS_EASYOCR = False

# Lazily created on first use — loading EasyOCR's recognition model takes a
# few seconds, so we don't want to pay that cost on every server startup,
# only the first time a certificate actually needs OCR.
_ocr_reader = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None and HAS_EASYOCR:
        try:
            _ocr_reader = easyocr.Reader(["en"], gpu=False)
        except Exception as e:
            print(f"[VERIFICATION] Failed to initialize EasyOCR reader: {e}")
            return None
    return _ocr_reader

# True if we have *any* working way to scan for a QR code — OpenCV alone is
# enough. Used by compute_verification_status to decide whether "no QR found"
# means a genuine finding vs. "we couldn't check at all".
HAS_QR_CAPABILITY = HAS_CV2 or HAS_PYZBAR

# Higher DPI gives QR detection more pixels to work with — real certificates
# often have a small QR in a corner that's easy to miss at low resolution.
RASTER_DPI = 150


def _rasterize_to_temp_image(path: str) -> Optional[str]:
    """
    If `path` is a PDF, renders its first page to a temporary PNG and returns
    that new path. If it's already an image, returns `path` unchanged.
    Returns None if rasterization isn't possible (missing library, bad file).
    """
    if not path.lower().endswith(".pdf"):
        return path

    if not HAS_FITZ:
        print("[VERIFICATION] PyMuPDF (fitz) not installed — cannot read PDF pages. "
              "Install with: pip install pymupdf")
        return None

    try:
        doc = fitz.open(path)
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=RASTER_DPI)
        temp_path = path + ".page1.png"
        pix.save(temp_path)
        doc.close()
        return temp_path
    except Exception as e:
        print(f"[VERIFICATION] Failed to rasterize PDF {path}: {e}")
        return None


def _qr_candidate_images(img):
    """
    Yields a few different preprocessed versions of the same image to try QR
    detection against. Real-world QR codes vary in contrast, size, and
    placement, so trying more than just the raw image meaningfully improves
    detection odds without adding any new dependencies.
    """
    yield img  # original, as-is

    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        yield cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        # Adaptive thresholding helps with low-contrast or slightly faded prints.
        thresh = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 5
        )
        yield cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)

        # Upscaling helps when the QR code is small relative to the page.
        upscaled = cv2.resize(img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        yield upscaled
    except Exception as e:
        print(f"[VERIFICATION] QR preprocessing step failed (continuing with what we have): {e}")


def _decode_qr_with_opencv(raster_path: str) -> Optional[str]:
    """Primary QR path — self-contained in opencv-python, no system DLLs needed."""
    try:
        img = cv2.imread(raster_path)
        if img is None:
            return None

        detector = cv2.QRCodeDetector()

        for candidate in _qr_candidate_images(img):
            try:
                data, points, _ = detector.detectAndDecode(candidate)
                if data:
                    return data
            except Exception:
                pass

            try:
                retval, decoded_info, _, _ = detector.detectAndDecodeMulti(candidate)
                if retval:
                    for text in decoded_info:
                        if text:
                            return text
            except Exception:
                pass

        return None
    except Exception as e:
        print(f"[VERIFICATION] OpenCV QR decode failed: {e}")
        return None


def _decode_qr_with_pyzbar(raster_path: str) -> Optional[str]:
    """Secondary QR path — only reached if OpenCV didn't find anything."""
    if not HAS_PYZBAR:
        return None
    try:
        if HAS_CV2:
            img = cv2.imread(raster_path)
            if img is None:
                return None
            decoded = zbar_decode(img)
        elif HAS_PIL:
            img = Image.open(raster_path)
            decoded = zbar_decode(img)
        else:
            return None
        if decoded:
            return decoded[0].data.decode("utf-8", errors="ignore")
        return None
    except Exception as e:
        print(f"[VERIFICATION] pyzbar QR decode failed: {e}")
        return None


# ---------- a) QR extraction ----------
def extract_qr_code(image_path: str) -> Optional[str]:
    """Returns the decoded text/URL from the first QR code found, or None."""
    if not HAS_QR_CAPABILITY:
        print("[VERIFICATION] No QR detection method available (need opencv-python "
              "and/or a working pyzbar install) — QR detection unavailable.")
        return None

    raster_path = _rasterize_to_temp_image(image_path)
    if raster_path is None or not os.path.exists(raster_path):
        return None

    try:
        result = None
        if HAS_CV2:
            result = _decode_qr_with_opencv(raster_path)
        if result is None and HAS_PYZBAR:
            result = _decode_qr_with_pyzbar(raster_path)
        if result is None:
            print(f"[VERIFICATION] No QR code found on {os.path.basename(image_path)} "
                  f"after trying original/grayscale/threshold/upscaled variants.")
        return result
    finally:
        # Clean up the temporary rasterized page if we created one
        if raster_path != image_path and os.path.exists(raster_path):
            try:
                os.remove(raster_path)
            except OSError:
                pass


# ---------- b) Domain check ----------
def check_qr_domain(qr_text: Optional[str], expected_domains: Optional[list[str]] = None) -> dict:
    """
    Returns:
        {"qr_found": bool, "qr_domain": str | None, "domain_trusted": bool | None}

    domain_trusted is None (not False) when no expected_domains list was given —
    there's a real difference between "we checked and it failed" and "we never
    checked at all", and callers/UIs should be able to tell those apart.
    """
    if not qr_text:
        return {"qr_found": False, "qr_domain": None, "domain_trusted": None}

    try:
        parsed = urlparse(qr_text if "://" in qr_text else f"https://{qr_text}")
        domain = (parsed.netloc or "").lower() or None
    except Exception:
        domain = None

    domain_trusted = None
    if expected_domains and domain:
        domain_trusted = any(
            domain == d.lower() or domain.endswith("." + d.lower()) for d in expected_domains
        )

    return {"qr_found": True, "qr_domain": domain, "domain_trusted": domain_trusted}


# ---------- b2) Certificate holder name check ----------
def check_certificate_holder(qr_text: Optional[str], account_name: Optional[str]) -> dict:
    """
    If the QR code's URL includes a `name` query parameter (as many real
    institutional verification links do — pointing to a page that displays
    the certificate holder's name), compares it against the logged-in
    student's account name. Catches the case where someone uploads a
    genuine, validly-domained certificate that simply isn't theirs.

    Returns:
        {"qr_name": str | None, "name_match": bool | None}

    name_match is None when there's nothing to check (no name in the QR,
    or no account name given) — same "checked vs. never checked" distinction
    as domain_trusted above.
    """
    if not qr_text:
        return {"qr_name": None, "name_match": None}

    try:
        parsed = urlparse(qr_text if "://" in qr_text else f"https://{qr_text}")
        params = parse_qs(parsed.query)
        qr_name = (params.get("name", [None])[0] or "").strip() or None
    except Exception:
        qr_name = None

    if not qr_name or not account_name:
        return {"qr_name": qr_name, "name_match": None}

    def _normalize(name: str) -> str:
        return re.sub(r"[^a-z]", "", name.lower())

    name_match = _normalize(qr_name) == _normalize(account_name)
    return {"qr_name": qr_name, "name_match": name_match}


# ---------- b2) Native PDF text extraction (works without OCR) ----------
def extract_certificate_text_native(file_path: str) -> Optional[str]:
    """
    Reads a PDF's actual embedded text layer directly — fundamentally
    different from OCR (which "looks" at an image and guesses letters from
    pixels). Most digitally-generated certificates (issued by a college
    portal or an official system, as opposed to a scanned photo of a printed
    page) have genuine text built into the file itself, which this reads
    instantly and with total accuracy — no image analysis, no heavy
    libraries, no memory risk. Returns None for image-only PDFs (e.g. a
    scanned photo of a paper certificate), which genuinely have no embedded
    text layer to read — that case still needs OCR, which remains a
    separate, optional fallback.
    """
    if not file_path.lower().endswith(".pdf") or not HAS_FITZ:
        return None
    try:
        doc = fitz.open(file_path)
        text = " ".join(page.get_text() for page in doc)
        doc.close()
        text = text.strip().lower()
        return text if text else None
    except Exception as e:
        print(f"[VERIFICATION] Native PDF text extraction failed: {e}")
        return None


# ---------- b3) OCR-based text extraction (fallback for scanned/image-only PDFs) ----------

# A starter list of well-known certificate issuers to look for in the OCR'd
# text. This is intentionally small and easy to extend — add more as you
# encounter real certificates that should be recognized. Matching is by
# substring, so "Coursera" also matches "Coursera Inc." etc.
KNOWN_ISSUER_KEYWORDS = [
    "coursera", "nptel", "cisco", "credly", "aicte", "udemy", "edx",
    "google", "ibm", "microsoft", "linkedin learning", "great learning",
    "simplilearn", "infosys springboard", "hackerrank",
]


def extract_certificate_text(file_path: str) -> Optional[str]:
    """
    Runs OCR over the certificate's first page and returns all recognized
    text as one lowercase string, or None if OCR isn't available or fails.

    This is the fallback for certificates that have NO embedded text layer AT
    ALL — typically a scanned photo of a printed page. Most digitally-issued
    certificates never reach this function at all, since
    extract_certificate_text_native() above already reads their real text
    directly, for free and instantly.

    Important honesty note: this is a WEAKER signal than a QR code linking
    to a trusted domain. Printed text can be faked far more easily than a
    working link to a real verification page. compute_verification_status
    reflects that difference in its notes rather than treating an OCR match
    as equally trustworthy.
    """
    if not HAS_EASYOCR:
        print("[VERIFICATION] EasyOCR not installed — cannot run text-based "
              "verification. Install with: pip install easyocr")
        return None

    raster_path = _rasterize_to_temp_image(file_path)
    if raster_path is None or not os.path.exists(raster_path):
        return None

    reader = _get_ocr_reader()
    if reader is None:
        return None

    try:
        results = reader.readtext(raster_path, detail=0)  # detail=0 -> just the text strings
        return " ".join(results).lower()
    except Exception as e:
        print(f"[VERIFICATION] OCR failed on {file_path}: {e}")
        return None


def check_certificate_text(
    ocr_text: Optional[str], account_name: Optional[str], account_college: Optional[str] = None
) -> dict:
    """
    Given the certificate's text (from either native PDF extraction or OCR),
    checks two things:
      1. Does the student's own name appear on the certificate?
      2. Does a recognized issuer's name appear on the certificate — either
         a well-known global platform (Coursera, Cisco, etc.) OR the
         student's own stated college name from their profile? Checking
         against the student's own college works for every student from
         every institution automatically, without needing every college in
         the world hardcoded into KNOWN_ISSUER_KEYWORDS.

    Returns:
        {"name_found": bool | None, "issuer_found": str | None}

    Both are None (not False) when no text was available at all, to
    keep the same "checked vs. never checked" distinction used elsewhere.
    """
    if not ocr_text:
        return {"name_found": None, "issuer_found": None}

    name_found = None
    if account_name:
        # Match on individual name parts rather than requiring the exact
        # full name in the exact order — text extraction and certificate
        # layouts often split or reorder a name (e.g. "Kavana" on one line,
        # "R" on the next).
        parts = [p for p in re.sub(r"[^a-zA-Z ]", "", account_name.lower()).split() if len(p) > 1]
        name_found = bool(parts) and all(part in ocr_text for part in parts)

    issuer_found = next((k for k in KNOWN_ISSUER_KEYWORDS if k in ocr_text), None)

    if not issuer_found and account_college:
        college_clean = account_college.strip().lower()
        if college_clean and college_clean in ocr_text:
            issuer_found = account_college.strip()

    return {"name_found": name_found, "issuer_found": issuer_found}


# ---------- c) Basic tamper heuristics ----------
def basic_image_tamper_checks(image_path: str) -> dict:
    """
    A few simple, explainable signals — NOT a forensic guarantee. This is
    intentionally lightweight: enough to flag obviously-edited files for
    human review, without overclaiming certainty in a demo.
    """
    raster_path = _rasterize_to_temp_image(image_path)
    signals = {
        "has_exif": False,
        "exif_software": None,
        "resolution": None,
        "analysis_available": False,
    }

    if raster_path is None or not os.path.exists(raster_path) or not HAS_PIL:
        return signals

    try:
        img = Image.open(raster_path)
        signals["resolution"] = list(img.size)
        signals["analysis_available"] = True

        exif_data = img._getexif() if hasattr(img, "_getexif") else None
        if exif_data:
            signals["has_exif"] = True
            for tag_id, value in exif_data.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                if tag == "Software":
                    signals["exif_software"] = str(value)
                    break
    except Exception as e:
        print(f"[VERIFICATION] Tamper check failed: {e}")
    finally:
        if raster_path != image_path and os.path.exists(raster_path):
            try:
                os.remove(raster_path)
            except OSError:
                pass

    return signals


# A few tools whose PDF export fingerprint is a real, meaningful red flag for
# a "certificate" — these are design/document tools, not certificate-issuing
# platforms, so a PDF that says one of these made it was very likely built by
# a person in a template editor rather than issued by a real institution.
SUSPICIOUS_PDF_PRODUCERS = ["canva", "figma", "photopea"]


def check_pdf_producer_metadata(file_path: str) -> dict:
    """
    Reads a PDF's own hidden "Producer" / "Creator" metadata fields — separate
    from, and often more durable than, image EXIF data — to check whether it
    was made in a design tool like Canva rather than issued by an institution.

    Many PDF exporters (including Canva) leave this field intact even when a
    user has stripped other metadata, since it's set by the export pipeline
    itself rather than something a casual user would think to remove.

    Returns:
        {"pdf_tool_found": str | None}

    None means either the file isn't a PDF, no matching tool was found, or
    metadata couldn't be read at all — same "checked vs. found nothing"
    honesty pattern used throughout this module. This is one real signal
    among several, not a standalone verdict — a certificate can still be
    genuine even if this comes back positive (e.g. a college may itself use
    Canva to design real certificates), so it feeds into the same overall
    scoring as everything else rather than being an automatic rejection.
    """
    if not file_path.lower().endswith(".pdf") or not HAS_FITZ:
        return {"pdf_tool_found": None}

    try:
        doc = fitz.open(file_path)
        meta = doc.metadata or {}
        doc.close()
        combined = f"{meta.get('producer', '')} {meta.get('creator', '')}".lower()
        found = next((tool for tool in SUSPICIOUS_PDF_PRODUCERS if tool in combined), None)
        return {"pdf_tool_found": found}
    except Exception as e:
        print(f"[VERIFICATION] PDF metadata check failed: {e}")
        return {"pdf_tool_found": None}


def check_pdf_has_design_elements(file_path: str) -> dict:
    """
    Checks whether the PDF contains any embedded images at all — logos,
    borders, seals, decorative graphics. Real certificates are almost always
    visually designed documents; a quickly faked one (typed text with no
    design work) frequently has none. This is one more honest signal, not a
    certainty check — someone determined could still add a logo image to a
    fake, and a text-only real letter genuinely exists too. It exists purely
    to raise the bar against the easiest, laziest kind of fake: plain typed
    text claiming to be a certificate.

    Returns:
        {"has_images": bool | None}

    None means the file isn't a PDF or couldn't be read — same "checked vs.
    couldn't check" honesty pattern used throughout this module.
    """
    if not file_path.lower().endswith(".pdf") or not HAS_FITZ:
        return {"has_images": None}
    try:
        doc = fitz.open(file_path)
        has_images = any(len(page.get_images()) > 0 for page in doc)
        doc.close()
        return {"has_images": has_images}
    except Exception as e:
        print(f"[VERIFICATION] Design-element check failed: {e}")
        return {"has_images": None}


# ---------- d) Compute overall status ----------
def compute_verification_status(
    qr_info: dict,
    tamper_signals: dict,
    holder_info: Optional[dict] = None,
    ocr_info: Optional[dict] = None,
    pdf_tool_info: Optional[dict] = None,
    design_info: Optional[dict] = None,
) -> tuple[str, dict]:
    """
    Simple, explainable rule set:

    - QR found + trusted domain + certificate holder name matches (or no
      name to check) + no editing-software hint -> "verified"
      (highest-confidence path: a working link to a real verification page)
    - QR found + trusted domain, but the certificate holder's name does NOT
      match the account -> "suspicious" (a validly-issued certificate that
      simply isn't this student's)
    - No QR, but the certificate's text (read natively from the PDF, or via
      OCR as a fallback) shows BOTH the student's own name AND a recognized
      issuer, AND the document has real design elements (a logo/seal/
      graphics, not just bare typed text), with no editing-software hint ->
      "verified", but explicitly noted as a text-match, not a cryptographic
      one — this is the fallback for real certificates that don't have a QR
      code, while still rejecting the laziest kind of fake (plain typed text
      with nothing else)
    - Same text/issuer match, but NO design elements at all -> "suspicious"
      (unusual for a real certificate — worth a manual check)
    - QR missing (and text didn't find a confident match), or domain
      untrusted/unknown, or an editing-software hint alone -> "suspicious"
    - Editing-software hint (image EXIF OR PDF producer/creator field)
      COMBINED with a missing/untrusted QR -> "rejected" (two independent
      red flags together, not just one shaky signal)
    - No QR detection method available at all on this machine -> "pending" (we
      genuinely don't know, so we shouldn't claim "suspicious" as if we checked
      and it failed)
    """
    holder_info = holder_info or {"qr_name": None, "name_match": None}
    ocr_info = ocr_info or {"name_found": None, "issuer_found": None}
    pdf_tool_info = pdf_tool_info or {"pdf_tool_found": None}
    design_info = design_info or {"has_images": None}

    if not HAS_QR_CAPABILITY:
        return "pending", {
            "qr_found": False,
            "qr_domain": None,
            "domain_trusted": None,
            "qr_name": None,
            "name_match": None,
            "ocr_name_found": None,
            "ocr_issuer_found": None,
            "tamper_signals": tamper_signals,
            "pdf_tool_found": None,
            "notes": "QR detection is unavailable on this server, so authenticity could not be automatically checked. Queued for manual review.",
        }

    qr_found = qr_info.get("qr_found", False)
    qr_domain = qr_info.get("qr_domain")
    domain_trusted = qr_info.get("domain_trusted")
    qr_name = holder_info.get("qr_name")
    name_match = holder_info.get("name_match")
    ocr_name_found = ocr_info.get("name_found")
    ocr_issuer_found = ocr_info.get("issuer_found")
    pdf_tool_found = pdf_tool_info.get("pdf_tool_found")
    has_images = design_info.get("has_images")

    editing_software_hint = False
    software = (tamper_signals.get("exif_software") or "").lower()
    if any(tool in software for tool in ["photoshop", "gimp", "canva"]):
        editing_software_hint = True
    # A design-tool fingerprint in the PDF's own metadata is just as strong a
    # signal as the image EXIF check above, so it triggers the same flag.
    if pdf_tool_found:
        editing_software_hint = True

    qr_problem = (not qr_found) or (domain_trusted is False)
    name_problem = name_match is False
    ocr_confident_match = bool(ocr_name_found) and bool(ocr_issuer_found)

    if editing_software_hint and qr_problem:
        status = "rejected"
        tool_name = tamper_signals.get("exif_software") or pdf_tool_found
        notes = (f"This document appears to have been made with {tool_name} "
                 "AND has no trustworthy QR code — flagged for rejection, but a human should confirm.")
    elif qr_found and domain_trusted and name_problem:
        status = "suspicious"
        notes = (f"QR code is from a trusted issuer, but the certificate holder name "
                 f"(\"{qr_name}\") doesn't match this account — this certificate may not belong to you.")
    elif qr_found and domain_trusted and not editing_software_hint:
        status = "verified"
        notes = "QR code found and matches a trusted issuer domain. No obvious editing signals detected."
    elif not qr_found and ocr_confident_match and not editing_software_hint and has_images:
        status = "verified"
        notes = (f"No QR code found, but the certificate text matches your account name, names "
                 f"a recognized issuer (\"{ocr_issuer_found}\"), and includes real design elements "
                 f"(logo/seal/graphics). Note: this is a text-based match, not a cryptographic one — "
                 f"weaker evidence than a verified QR code, but a real signal for certificates that "
                 f"don't include a QR code at all.")
    elif not qr_found and ocr_confident_match and not editing_software_hint and has_images is False:
        status = "suspicious"
        notes = (f"The certificate text names you and a recognized issuer (\"{ocr_issuer_found}\"), "
                 f"but the document has no logo, seal, or design elements at all — unusual for a real "
                 f"certificate and worth a manual check.")
    elif not qr_found:
        status = "suspicious"
        if pdf_tool_found:
            notes = f"No QR code detected, and this PDF's metadata indicates it was made with {pdf_tool_found.title()} — a design tool, not a certificate issuer."
        elif ocr_name_found is None:
            notes = "No QR code detected, and certificate text could not be read for a name/issuer check."
        elif not ocr_name_found:
            notes = "No QR code detected, and the certificate text doesn't appear to include your account name."
        elif not ocr_issuer_found:
            notes = "No QR code detected, and no recognized issuer name was found on the certificate."
        else:
            notes = "No QR code detected — could not confirm issuer authenticity."
    elif qr_found and domain_trusted is False:
        status = "suspicious"
        notes = "QR code found, but its domain doesn't match any trusted issuer — flagged for manual review."
    elif qr_found and domain_trusted is None:
        status = "suspicious"
        notes = "QR code found, but no trusted-domain list was configured to check it against."
    elif editing_software_hint:
        status = "suspicious"
        tool_name = tamper_signals.get("exif_software") or pdf_tool_found
        notes = f"This document's metadata indicates it may have been made with {tool_name}."
    else:
        status = "suspicious"
        notes = "Could not fully confirm authenticity — flagged for manual review."

    details = {
        "qr_found": bool(qr_found),
        "qr_domain": qr_domain,
        "domain_trusted": domain_trusted,
        "qr_name": qr_name,
        "name_match": name_match,
        "ocr_name_found": ocr_name_found,
        "ocr_issuer_found": ocr_issuer_found,
        "tamper_signals": tamper_signals,
        "pdf_tool_found": pdf_tool_found,
        "has_images": has_images,
        "notes": notes,
    }
    return status, details
