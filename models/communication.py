"""Communication models — emails and phone transcripts.

These are the primary evidence sources the agent uses to resolve informal
modification exceptions. An ExceptionRecord links to zero or more emails
and transcripts via ID arrays; the agent reads them to understand what was
agreed verbally or in writing before the PO was issued.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class Email(BaseModel):
    """An email communication between a Meridian Corp buyer and a supplier contact."""

    email_id: str
    subject: str
    sender: str
    receiver: str
    date: date
    body: str
    related_po: str | None = None
    related_invoice: str | None = None


class PhoneTranscript(BaseModel):
    """A phone call transcript between a Meridian Corp employee and a supplier rep."""

    transcript_id: str
    caller: str
    caller_organization: str
    callee: str
    callee_organization: str
    date: date
    duration_minutes: int
    transcript: str
    related_po: str | None = None
    related_invoice: str | None = None
