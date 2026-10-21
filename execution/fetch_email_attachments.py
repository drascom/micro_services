#!/usr/bin/env python3
"""
Script: Fetch Email Attachments
Purpose: Retrieve PDF attachments from an email by UID via IMAP
Usage: python execution/fetch_email_attachments.py <email_uid>

Inputs:
    - email_uid: The UID of the email to fetch attachments from

Outputs:
    - JSON object with status and list of PDF attachments (filename, content as base64)
"""

import imaplib
import email
from email.header import decode_header
import base64
import json
import sys
from typing import Optional
from dataclasses import dataclass, asdict

from config import get_imap_config, is_questionnaire_filename, ImapConfig


@dataclass
class Attachment:
    """Represents an email attachment."""
    filename: str
    content_base64: str
    content_type: str
    size_bytes: int
    is_questionnaire: bool


@dataclass
class FetchResult:
    """Result of fetching email attachments."""
    status: str
    email_uid: str
    attachments: list[dict]
    questionnaire_count: int
    message: Optional[str] = None


def decode_mime_header(header_value: str) -> str:
    """Decode a MIME-encoded header value."""
    if not header_value:
        return ""

    decoded_parts = decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            charset = charset or "utf-8"
            try:
                result.append(part.decode(charset))
            except (UnicodeDecodeError, LookupError):
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def connect_imap(config: ImapConfig) -> imaplib.IMAP4_SSL:
    """Connect to IMAP server and authenticate."""
    if config.use_ssl:
        mail = imaplib.IMAP4_SSL(config.server, config.port)
    else:
        mail = imaplib.IMAP4(config.server, config.port)

    mail.login(config.username, config.password)
    mail.select(config.mailbox)
    return mail


def fetch_attachments(email_uid: str) -> FetchResult:
    """
    Fetch PDF attachments from an email by UID.

    Args:
        email_uid: The UID of the email to fetch

    Returns:
        FetchResult with status and attachment list
    """
    config = get_imap_config()

    # Validate config
    if not config.server or not config.username or not config.password:
        return FetchResult(
            status="error",
            email_uid=email_uid,
            attachments=[],
            questionnaire_count=0,
            message="IMAP configuration is incomplete"
        )

    try:
        mail = connect_imap(config)
    except imaplib.IMAP4.error as e:
        return FetchResult(
            status="error",
            email_uid=email_uid,
            attachments=[],
            questionnaire_count=0,
            message=f"IMAP connection failed: {str(e)}"
        )

    try:
        # Fetch the email by UID
        result, data = mail.uid("fetch", email_uid.encode(), "(RFC822)")

        if result != "OK" or not data or data[0] is None:
            return FetchResult(
                status="not_found",
                email_uid=email_uid,
                attachments=[],
                questionnaire_count=0,
                message=f"Email with UID {email_uid} not found"
            )

        # Parse the email
        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        attachments = []

        # Walk through email parts
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # Check if this is a PDF attachment
            if "attachment" in content_disposition or content_type == "application/pdf":
                filename = part.get_filename()
                if filename:
                    filename = decode_mime_header(filename)
                else:
                    filename = "unnamed.pdf"

                # Only process PDF files
                if not filename.lower().endswith(".pdf") and content_type != "application/pdf":
                    continue

                # Get the attachment content
                payload = part.get_payload(decode=True)
                if payload:
                    content_b64 = base64.b64encode(payload).decode("utf-8")

                    attachment = Attachment(
                        filename=filename,
                        content_base64=content_b64,
                        content_type=content_type,
                        size_bytes=len(payload),
                        is_questionnaire=is_questionnaire_filename(filename)
                    )
                    attachments.append(asdict(attachment))

        questionnaire_count = sum(1 for a in attachments if a["is_questionnaire"])

        return FetchResult(
            status="success",
            email_uid=email_uid,
            attachments=attachments,
            questionnaire_count=questionnaire_count,
            message=None
        )

    except Exception as e:
        return FetchResult(
            status="error",
            email_uid=email_uid,
            attachments=[],
            questionnaire_count=0,
            message=f"Error processing email: {str(e)}"
        )
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(json.dumps({
            "status": "error",
            "message": "Usage: python fetch_email_attachments.py <email_uid>"
        }))
        sys.exit(1)

    email_uid = sys.argv[1]
    result = fetch_attachments(email_uid)
    print(json.dumps(asdict(result), indent=2))

    # Exit with appropriate code
    if result.status == "error":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
