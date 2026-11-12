"""PhishGuard - AI phishing, BEC and malware e-mail protection.

Connects to Microsoft 365 (Graph API) or sits in front of any mail server
as an SMTP gateway. Every message is scored on sender authentication,
lookalike domains, language (heuristics + Claude), links and attachments;
threats are quarantined, users are warned, and confirmed threats are
purged from every mailbox in the organisation.
"""

__version__ = "1.0.0"
