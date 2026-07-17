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
            
            html += f"""
                <div style="margin-bottom: 25px; padding: 15px; background-color: #fff8f0; border-left: 4px solid #f0ad4e;">
                    <h3 style="margin-top: 0; color: #f0ad4e;">✎ MODIFIED REQUIREMENT</h3>
                    <p style="font-size: 0.9em; color: #666; margin: 0 0 10px 0;">{cat_str}</p>
                    <p style="margin: 0; color: #d9534f; text-decoration: line-through;"><b>Before:</b> {req.get('old_text', 'N/A')}</p>
                    <p style="margin: 10px 0 0 0; color: #5cb85c;"><b>After:</b> {req['requirement_text']}</p>
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
        
    def _send_email(self, recipients: List[str], subject: str, html_body: str) -> bool:
        """Sends the email using smtplib"""
        if not self.host or not self.user:
            logger.error("SMTP settings are not fully configured in .env. Cannot send email.")
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
