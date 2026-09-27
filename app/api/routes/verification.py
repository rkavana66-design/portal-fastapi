from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db, SessionLocal
from app.api.deps import get_current_user, require_role
from app.models.models import User, UserRole, Document, Student
from app.schemas.verification import (
    DocumentVerificationResponse, ManualVerifyRequest, AdminDocumentListItem,
    ExternalProfileVerificationResponse, GithubVerification, LeetcodeVerification,
    LinkedinVerification, PortfolioVerification,
)
from app.utils.verification import (
    extract_qr_code, check_qr_domain, check_certificate_holder, basic_image_tamper_checks,
    extract_certificate_text, check_certificate_text, check_pdf_producer_metadata,
    compute_verification_status,
)
from app.utils.external_verification import (
    verify_github_profile, verify_leetcode_profile,
    verify_linkedin_profile, verify_portfolio_url,
)
from app.core.config import settings

router = APIRouter(prefix="/api/verification", tags=["verification"])


def _trusted_domains() -> list[str]:
    return [d.strip() for d in settings.trusted_issuer_domains.split(",") if d.strip()]


def _get_document_or_404(document_id: UUID, db: Session) -> Document:
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


# ---------- Background worker: does the actual (slow) scanning work ----------
def _run_document_scan(document_id: UUID) -> None:
    """
    Runs the full QR + OCR + tamper analysis and writes the final result to
    the database. Executes in a background task, AFTER the HTTP response
    has already been sent — so it needs its own database session, since the
    request's session closes as soon as the response goes out.

    This exists specifically because OCR (EasyOCR/PyTorch) can take anywhere
    from a few seconds to a few minutes, especially on its first run. Making
    the student's browser wait synchronously for that risks a real timeout
    on most hosting platforms (many kill requests around 30 seconds) — so
    the upload returns instantly with "pending", and this function updates
    the real result a bit later. The frontend polls for it.
    """
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if document is None or not document.file_path:
            return

        owner = db.query(Student).filter(Student.id == document.student_id).first()
        account_name = owner.name if owner else None

        qr_text = extract_qr_code(document.file_path)
        qr_info = check_qr_domain(qr_text, _trusted_domains())
        holder_info = check_certificate_holder(qr_text, account_name)
        tamper_signals = basic_image_tamper_checks(document.file_path)
        pdf_tool_info = check_pdf_producer_metadata(document.file_path)

        ocr_info = None
        if not qr_info.get("qr_found"):
            ocr_text = extract_certificate_text(document.file_path)
            ocr_info = check_certificate_text(ocr_text, account_name)

        status_value, details = compute_verification_status(
            qr_info=qr_info, tamper_signals=tamper_signals, holder_info=holder_info,
            ocr_info=ocr_info, pdf_tool_info=pdf_tool_info,
        )

        document.verification_status = status_value
        document.verification_details = details
        document.qr_found = qr_info["qr_found"]
        document.qr_domain = qr_info["qr_domain"]
        db.commit()
    except Exception as e:
        # Never let a scanning failure crash silently with no trace — mark
        # the document as pending-for-manual-review rather than leaving it
        # stuck on whatever status it had before, and log the real error.
        print(f"[VERIFICATION] Background scan failed for document {document_id}: {e}")
        try:
            document = db.query(Document).filter(Document.id == document_id).first()
            if document is not None:
                document.verification_status = "pending"
                document.verification_details = {
                    "notes": "Automatic scan failed unexpectedly — queued for manual review."
                }
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------- a) SCAN DOCUMENT ----------
@router.post("/scan-document/{document_id}", response_model=DocumentVerificationResponse)
def scan_document(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),  # any logged-in user for now, per spec
    db: Session = Depends(get_db),
):
    document = _get_document_or_404(document_id, db)

    if not document.file_path:
        raise HTTPException(status_code=400, detail="Document has no file on disk to scan")

    # Return immediately — the actual scan (which can take anywhere from a
    # couple seconds to a couple minutes, especially with OCR involved) runs
    # in the background. The frontend should poll
    # GET /document/{id}/verification until the status is no longer "pending".
    document.verification_status = "pending"
    document.verification_details = {"notes": "Scan in progress…"}
    db.commit()
    db.refresh(document)

    background_tasks.add_task(_run_document_scan, document.id)

    return DocumentVerificationResponse(
        id=document.id,
        type=document.type,
        file_path=document.file_path,
        verification_status=document.verification_status,
        verification_details=document.verification_details,
        message="Scan started — check back shortly for the result.",
    )


# ---------- b) MANUAL OVERRIDE ----------
@router.post("/manual-verify-document/{document_id}", response_model=DocumentVerificationResponse)
def manual_verify_document(
    document_id: UUID,
    payload: ManualVerifyRequest,
    current_user: User = Depends(require_role(UserRole.admin)),  # tighten/loosen for your demo as needed
    db: Session = Depends(get_db),
):
    document = _get_document_or_404(document_id, db)

    document.verification_status = payload.status
    details = document.verification_details or {}
    details["manual_notes"] = payload.notes
    details["manually_reviewed_by"] = str(current_user.id)
    document.verification_details = details
    db.commit()
    db.refresh(document)

    return DocumentVerificationResponse(
        id=document.id,
        type=document.type,
        file_path=document.file_path,
        verification_status=document.verification_status,
        verification_details=document.verification_details,
        message="Manual verification recorded.",
    )


# ---------- c) GET VERIFICATION STATUS ----------
@router.get("/document/{document_id}/verification", response_model=DocumentVerificationResponse)
def get_document_verification(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = _get_document_or_404(document_id, db)
    return DocumentVerificationResponse(
        id=document.id,
        type=document.type,
        file_path=document.file_path,
        verification_status=document.verification_status,
        verification_details=document.verification_details,
        message="Current verification status.",
    )


# ---------- f) ADMIN: list all documents ----------
@router.get("/admin/documents", response_model=list[AdminDocumentListItem])
def list_all_documents(
    current_user: User = Depends(require_role(UserRole.admin)),
    db: Session = Depends(get_db),
):
    documents = db.query(Document).order_by(Document.created_at.desc()).all()
    return [
        AdminDocumentListItem(
            id=d.id,
            student_id=d.student_id,
            type=d.type,
            verification_status=d.verification_status,
            created_at=d.created_at.isoformat() if d.created_at else "",
        )
        for d in documents
    ]


# ---------- d) VERIFY EXTERNAL PROFILES ----------
@router.post("/verify-external-profiles", response_model=ExternalProfileVerificationResponse)
def verify_external_profiles(
    current_user: User = Depends(require_role(UserRole.student)),
    db: Session = Depends(get_db),
):
    student = db.query(Student).filter(Student.user_id == current_user.id).first()
    if student is None:
        raise HTTPException(status_code=404, detail="Student profile not found for this account")

    github_result = verify_github_profile(student.github_url)
    leetcode_result = verify_leetcode_profile(student.leetcode_url)
    linkedin_result = verify_linkedin_profile(student.linkedin_url)
    portfolio_result = verify_portfolio_url(student.portfolio_url)

    # Persist what we learned back onto the student record
    if github_result["github_verified"]:
        student.github_username = github_result["github_username"]
    if leetcode_result["leetcode_verified"]:
        student.leetcode_username = leetcode_result["leetcode_username"]
        if leetcode_result["leetcode_rating"] is not None:
            student.leetcode_rating = leetcode_result["leetcode_rating"]

    db.commit()
    db.refresh(student)

    return ExternalProfileVerificationResponse(
        github=GithubVerification(**github_result),
        leetcode=LeetcodeVerification(**leetcode_result),
        linkedin=LinkedinVerification(**linkedin_result),
        portfolio=PortfolioVerification(**portfolio_result),
    )
