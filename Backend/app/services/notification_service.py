import asyncio
import html as html_lib
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Any
import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.config import settings
from app.models.database import TeamSubscription, Customer, Transcript
from sqlalchemy import func
from datetime import datetime

logger = logging.getLogger(__name__)

class NotificationService:
    """Handles Proof of Change emails"""
    
    def __init__(self):
        self.host = settings.SMTP_HOST
        self.port = settings.SMTP_PORT
        self.user = settings.SMTP_USER
        self.password = settings.SMTP_PASSWORD
        self.from_email = settings.SMTP_FROM_EMAIL
        
    async def send_change_notification(
        self, 
        db: AsyncSession, 
        customer_id: uuid.UUID, 
        session_name: str, 
        call_date: datetime,
        processed_reqs: List[Dict[str, Any]]
    ) -> bool:
        """
        Filters the processed requirements for changes (added/modified/removed),
        generates the HTML diff, and emails subscribed team members.
        """
        # Check if this is the FIRST transcript for this customer.
        # If only 1 transcript exists, this is the initial upload — no comparison baseline yet.
        # Skip email entirely on the first upload.
        transcript_count_result = await db.execute(
            select(func.count()).select_from(Transcript)
            .filter(Transcript.customer_id == customer_id)
        )
        transcript_count = transcript_count_result.scalar() or 0
        
        if transcript_count <= 1:
            logger.info(f"First transcript upload for customer {customer_id}. Skipping email notification.")
            return True
        
        # Only notify if existing requirements were MODIFIED (not new 'added' ones).
        # The email is "Proof of Change" — it should only fire when something changed
        # from a previous meeting, not when a brand new topic is discussed.
        changed_reqs = [r for r in processed_reqs if r['change_type'] == 'modified']
        
        # Get Customer Info (needed for both cases)
        result_cust = await db.execute(select(Customer).filter(Customer.id == customer_id))
        customer = result_cust.scalars().first()
        customer_name = customer.name if customer else "Unknown Customer"
        
        # Get Subscribers
        result_sub = await db.execute(
            select(TeamSubscription).filter(
                TeamSubscription.customer_id == customer_id,
                TeamSubscription.is_active == True
            )
        )
        subscribers = result_sub.scalars().all()
        
        if not subscribers:
            logger.warning(f"No active email subscribers for customer {customer_name}. Skipping email.")
            return True
        
        recipient_emails = [sub.email_address for sub in subscribers]
        date_str = call_date.strftime("%Y-%m-%d")
        
        if not changed_reqs:
            # Send "No Changes" confirmation email
            logger.info("No modified requirements found. Sending confirmation email.")
            subject = f"✅ No Requirement Changes — {customer_name} | {session_name}"
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                    <h2 style="color: #5cb85c;">✅ No Requirement Changes Detected</h2>
                    <p>The transcript for <b>{session_name}</b> on <b>{date_str}</b> for <b>{customer_name}</b> has been processed successfully.</p>
                    <p>After comparing with previous requirements, <b>no modifications</b> were detected. All existing requirements remain unchanged.</p>
                    <br>
                    <p style="font-size: 0.8em; color: #aaa;">This is an automated message from the Requirement Tracking System.</p>
                </body>
            </html>
            """
            return self._send_email(recipients=recipient_emails, subject=subject, html_body=html_body)
        
        # Generate HTML Body for modified requirements
        html_body = self._generate_html_email(
            customer_name=customer_name,
            session_name=session_name,
            call_date=call_date,
            changed_reqs=changed_reqs
        )
        
        subject = f"⚠️ Requirement Changes Detected — {customer_name} | {session_name}"
        
        return self._send_email(
            recipients=recipient_emails,
            subject=subject,
            html_body=html_body
        )
        
    def _generate_html_email(self, customer_name: str, session_name: str, call_date: datetime, changed_reqs: List[Dict[str, Any]]) -> str:
        """Builds the HTML Proof of Change Email"""
        
        date_str = call_date.strftime("%Y-%m-%d")
        modified_count = len(changed_reqs)
        
        html = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <h2 style="color: #d9534f;">⚠️ Requirement Changes Detected</h2>
                <p>This email is proof that <b>{modified_count}</b> existing requirement(s) were <b>modified</b> during <b>{session_name}</b> on <b>{date_str}</b> for <b>{customer_name}</b>.</p>
                <hr style="border-top: 1px solid #ccc; margin: 20px 0;">
        """
        
        for req in changed_reqs:
            category = req.get('category', 'General')
            sub = req.get('sub_category', '')
            cat_str = f"[{category} &gt; {sub}]" if sub else f"[{category}]"

            # One-line reason this counts as a change (headline). Hidden if not available.
            change_summary = (req.get('change_summary') or '').strip()
            summary_html = (
                f'<p style="margin: 0 0 12px 0; color: #333;"><b>What changed:</b> {change_summary}</p>'
                if change_summary else ''
            )
            # Time context: which call/date the OLD version came from, and this call for the new.
            when_parts = [p for p in [req.get('old_session') or '', req.get('old_date') or ''] if p]
            before_when = f" (from {' · '.join(when_parts)})" if when_parts else ""
            after_when = f" (this call — {session_name} · {date_str})"

            html += f"""
                <div style="margin-bottom: 25px; padding: 15px; background-color: #fff8f0; border-left: 4px solid #f0ad4e;">
                    <h3 style="margin-top: 0; color: #f0ad4e;">✎ MODIFIED REQUIREMENT</h3>
                    <p style="font-size: 0.9em; color: #666; margin: 0 0 10px 0;">{cat_str}</p>
                    {summary_html}
                    <p style="margin: 0;"><b style="color: #d9534f;">Before{before_when}:</b> <span style="color: #d9534f; text-decoration: line-through;">{req.get('old_text', 'N/A')}</span></p>
                    <p style="margin: 10px 0 0 0;"><b style="color: #5cb85c;">After{after_when}:</b> <span style="color: #5cb85c;">{req['requirement_text']}</span></p>
                    <p style="font-size: 0.85em; color: #888; margin: 10px 0 0 0;">Changed by: {req['confirmed_by']}</p>
                </div>
                """
                
        html += """
            <br>
            <p style="font-size: 0.8em; color: #aaa;">This is an automated message from the Requirement Tracking System.</p>
            </body>
        </html>
        """
        return html
        
    # ── Minutes of Meeting ───────────────────────────────────────────────────
    # Separate from the change-notification path above: different audience semantics, different
    # body, different trigger. Nothing above is modified.

    @staticmethod
    def _markdown_to_html(md: str) -> str:
        """
        Convert the narrow markdown subset the MOM prompt emits (h1-h3, bullets, bold, inline
        code) into HTML.

        Escapes FIRST, then applies formatting. This is LLM-generated text going into an email —
        if it ever contains angle brackets (and requirements about HTML attributes routinely do,
        e.g. 'Do not use target=_blank'), unescaped output would break the message or worse.
        A markdown library would allow raw HTML through by default, which is why this is hand-rolled.
        """
        out: List[str] = []
        in_list = False

        def inline(text: str) -> str:
            text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
            text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
            return text

        for raw in (md or "").split("\n"):
            line = html_lib.escape(raw.rstrip())
            stripped = line.strip()

            if not stripped:
                if in_list:
                    out.append("</ul>")
                    in_list = False
                continue

            heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            bullet = re.match(r"^[-*]\s+(.*)$", stripped)

            if heading or not bullet:
                if in_list:
                    out.append("</ul>")
                    in_list = False

            if heading:
                level = min(len(heading.group(1)), 3) + 1   # '#' -> h2, so the email title stays h1
                out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            elif bullet:
                if not in_list:
                    out.append("<ul>")
                    in_list = True
                out.append(f"<li>{inline(bullet.group(1))}</li>")
            else:
                out.append(f"<p>{inline(stripped)}</p>")

        if in_list:
            out.append("</ul>")
        return "\n".join(out)

    def _generate_mom_html(
        self,
        customer_name: str,
        session_name: str,
        call_date: datetime,
        content_markdown: str,
        truncated: bool = False,
    ) -> str:
        """Wrap the rendered minutes in the same inline-CSS idiom as the change email."""
        date_str = call_date.strftime("%d %b %Y") if call_date else session_name
        body = self._markdown_to_html(content_markdown)

        warning = ""
        if truncated:
            # Never let an incomplete document look complete to the reader.
            warning = (
                '<div style="background:#fff4e5;border-left:4px solid #ff9800;padding:12px 16px;'
                'margin:16px 0;">'
                '<strong>Note:</strong> these minutes reached the generation length limit and may '
                'be incomplete toward the end.</div>'
            )

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;
                     max-width: 800px; margin: 0 auto; padding: 20px;">
            <h1 style="color:#0d76ff; border-bottom:2px solid #eee; padding-bottom:8px;">
                Minutes of Meeting
            </h1>
            <p style="color:#666; margin-top:0;">
                <strong>{html_lib.escape(customer_name or '')}</strong> &nbsp;|&nbsp;
                {html_lib.escape(session_name or '')} &nbsp;|&nbsp; {date_str}
            </p>
            {warning}
            <div>{body}</div>
            <hr style="border:none;border-top:1px solid #eee;margin-top:32px;">
            <p style="color:#999;font-size:12px;">
                Generated automatically from the call transcript.
            </p>
        </body>
        </html>
        """

    async def send_mom_email(
        self,
        db: AsyncSession,
        customer_id: uuid.UUID,
        session_name: str,
        call_date: datetime,
        content_markdown: str,
        truncated: bool = False,
    ) -> Dict[str, Any]:
        """
        Email the minutes to this customer's active subscribers.

        Returns {"success", "recipients", "error"}. The caller persists the outcome — this
        service deliberately knows nothing about the MeetingMinutes table.
        """
        result: Dict[str, Any] = {"success": False, "recipients": [], "error": ""}

        if not content_markdown or not content_markdown.strip():
            result["error"] = "Refusing to send: these minutes have no content."
            logger.error(result["error"])
            return result

        customer = (await db.execute(
            select(Customer).filter(Customer.id == customer_id)
        )).scalars().first()
        customer_name = customer.name if customer else ""

        subscribers = (await db.execute(
            select(TeamSubscription).filter(
                TeamSubscription.customer_id == customer_id,
                TeamSubscription.is_active == True
            )
        )).scalars().all()

        # De-duplicate case-insensitively. The subscriptions endpoint compares addresses
        # case-sensitively when guarding against duplicates, so the same person can be stored
        # twice as "Name@x.com" and "name@x.com" — without this they would receive two copies.
        recipients, seen = [], set()
        for sub in subscribers:
            addr = (sub.email_address or "").strip()
            key = addr.lower()
            if addr and key not in seen:
                seen.add(key)
                recipients.append(addr)

        if not recipients:
            result["error"] = (
                "No active subscribers for this project. Add recipients via "
                "POST /api/subscriptions/ before sending."
            )
            logger.error(result["error"])
            return result

        subject = f"Minutes of Meeting - {customer_name} - {session_name}".strip(" -")
        html_body = self._generate_mom_html(
            customer_name, session_name, call_date, content_markdown, truncated
        )

        # _send_email uses blocking smtplib. This method is awaited from a FastAPI request, so
        # run it off the event loop or the whole API stalls for the SMTP round-trip.
        sent = await asyncio.to_thread(self._send_email, recipients, subject, html_body)

        result["recipients"] = recipients
        result["success"] = bool(sent)
        if not sent:
            result["error"] = "SMTP send failed — check SMTP settings and the server log."
        return result

    def _send_email(self, recipients: List[str], subject: str, html_body: str) -> bool:
        """Sends the email using smtplib"""
        if not self.host or not self.user or not self.from_email:
            logger.error(
                "SMTP settings are not fully configured in .env "
                "(need SMTP_HOST, SMTP_USER and SMTP_FROM_EMAIL). Cannot send email."
            )
            return False
            
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = ", ".join(recipients)
            
            part = MIMEText(html_body, "html")
            msg.attach(part)
            
            server = smtplib.SMTP(self.host, self.port)
            server.starttls()
            server.login(self.user, self.password)
            server.sendmail(self.from_email, recipients, msg.as_string())
            server.quit()
            
            logger.info(f"Successfully sent change notification email to {len(recipients)} recipients.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            return False
