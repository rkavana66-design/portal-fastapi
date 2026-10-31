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

    - QR found + trusted domain + name matches (or nothing to check) + no
      editing-software hint -> "verified"
    - QR found + trusted domain, but name does NOT match -> "suspicious"
    - No QR, text shows the student's name AND a recognized issuer, with real
      design elements and no editing-software hint -> "verified" (text match)
    - No QR, text matches but NO design elements -> "suspicious"
    - No QR and the file is a photo/scan whose text could not be read ->
      "pending" (an admin reviews it)
    - No QR, name found but issuer not on the recognized list (for example a
      college hackathon) -> "pending" (an admin reviews it)
    - No QR, text readable but the student's name is not on it -> "suspicious"
    - Editing-software hint combined with a missing/untrusted QR -> "rejected"
    - No QR detection method available at all -> "pending"
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
        if pdf_tool_found:
            status = "suspicious"
            notes = f"No QR code detected, and this PDF's metadata indicates it was made with {pdf_tool_found.title()} — a design tool, not a certificate issuer."
        elif ocr_name_found is None:
            status = "pending"
            notes = "No QR code found, and this looks like a photo or scan whose text could not be read automatically. Sent to an admin for review."
        elif not ocr_name_found:
            status = "suspicious"
            notes = "No QR code detected, and the certificate text doesn't appear to include your account name."
        elif not ocr_issuer_found:
            status = "pending"
            notes = "No QR code found. Your name appears on the certificate, but the issuer isn't on the recognized list (for example a college event). Sent to an admin for review."
        else:
            status = "pending"
            notes = "No QR code found. Your name and issuer appear on the certificate, but it could not be fully confirmed automatically. Sent to an admin for review."
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
