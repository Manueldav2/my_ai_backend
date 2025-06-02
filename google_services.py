import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import base64
from email.mime.text import MIMEText
import logging
from supabase import create_client, Client

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Supabase client
supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_SERVICE_KEY')
supabase: Client = create_client(supabase_url, supabase_key)

def get_user_credentials(user_id: str) -> dict:
    """
    Retrieve user's Google credentials from Supabase.
    """
    try:
        response = supabase.table('user_credentials').select('*').eq('user_id', user_id).execute()
        if not response.data:
            raise Exception("No credentials found for user")
        return response.data[0]
    except Exception as e:
        logger.error(f"Error getting user credentials: {str(e)}")
        raise

def create_credentials(token_info: dict) -> Credentials:
    """
    Create a Credentials object from token information.
    """
    return Credentials(
        token=token_info['access_token'],
        refresh_token=token_info['refresh_token'],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv('GOOGLE_CLIENT_ID'),
        client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
        scopes=token_info['scopes']
    )

def get_calendar_service(user_id: str):
    """
    Get an authorized Calendar API service instance using user-specific credentials.
    """
    try:
        # Get user credentials from Supabase
        token_info = get_user_credentials(user_id)
        
        # Create credentials object
        creds = create_credentials(token_info)
        
        # Build and return the service
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        logger.error(f"Error getting calendar service: {str(e)}")
        raise

def get_gmail_service(user_id: str):
    """
    Get an authorized Gmail API service instance using user-specific credentials.
    """
    try:
        # Get user credentials from Supabase
        token_info = get_user_credentials(user_id)
        
        # Create credentials object
        creds = create_credentials(token_info)
        
        # Build and return the service
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        logger.error(f"Error getting Gmail service: {str(e)}")
        raise

def get_upcoming_events(service, max_results=3):
    """
    Get a list of upcoming calendar events.
    """
    try:
        now = datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        return events_result.get('items', [])
    except Exception as e:
        logger.error(f"Error getting upcoming events: {str(e)}")
        raise

def create_calendar_event(service, event_details):
    """
    Create a calendar event with the specified details.
    """
    try:
        event = {
            'summary': event_details.get('summary'),
            'location': event_details.get('location'),
            'description': event_details.get('description'),
            'start': {
                'dateTime': event_details.get('start_time'),
                'timeZone': event_details.get('timezone', 'UTC'),
            },
            'end': {
                'dateTime': event_details.get('end_time'),
                'timeZone': event_details.get('timezone', 'UTC'),
            }
        }
        
        # Add recurrence if specified
        if event_details.get('recurrence'):
            event['recurrence'] = [event_details['recurrence']]
            
        # Add attendees if specified
        if event_details.get('attendees'):
            event['attendees'] = [{'email': email} for email in event_details['attendees']]
            
        # Add reminders if specified
        if event_details.get('reminders'):
            event['reminders'] = event_details['reminders']
            
        return service.events().insert(calendarId='primary', body=event).execute()
    except Exception as e:
        logger.error(f"Error creating calendar event: {str(e)}")
        raise

def send_email(service, to_email, subject, body):
    """
    Send an email using the Gmail API.
    """
    try:
        message = MIMEText(body)
        message['to'] = to_email
        message['subject'] = subject
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        
        return service.users().messages().send(
            userId='me',
            body={'raw': raw_message}
        ).execute()
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        raise

def get_recent_emails(service, max_results=10):
    """
    Get recent emails from the user's inbox.
    """
    try:
        results = service.users().messages().list(
            userId='me',
            maxResults=max_results,
            labelIds=['INBOX']
        ).execute()
        
        messages = results.get('messages', [])
        emails = []
        
        for msg in messages:
            email = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()
            
            headers = email['payload']['headers']
            email_data = {
                'id': email['id'],
                'threadId': email['threadId'],
                'from': next((h['value'] for h in headers if h['name'] == 'From'), ''),
                'subject': next((h['value'] for h in headers if h['name'] == 'Subject'), ''),
                'date': next((h['value'] for h in headers if h['name'] == 'Date'), '')
            }
            emails.append(email_data)
            
        return emails
    except Exception as e:
        logger.error(f"Error getting recent emails: {str(e)}")
        raise

def get_email_content(service, email_id):
    """
    Get the full content of a specific email.
    """
    try:
        email = service.users().messages().get(
            userId='me',
            id=email_id,
            format='full'
        ).execute()
        
        # Get email body
        if 'data' in email['payload'].get('body', {}):
            body = base64.urlsafe_b64decode(
                email['payload']['body']['data'].encode('UTF-8')
            ).decode('utf-8')
        elif 'parts' in email['payload']:
            parts = email['payload']['parts']
            body = ''
            for part in parts:
                if part.get('mimeType') == 'text/plain' and 'data' in part.get('body', {}):
                    body += base64.urlsafe_b64decode(
                        part['body']['data'].encode('UTF-8')
                    ).decode('utf-8')
        else:
            body = 'No content found'
            
        # Get headers
        headers = email['payload']['headers']
        email_data = {
            'id': email['id'],
            'threadId': email['threadId'],
            'from': next((h['value'] for h in headers if h['name'] == 'From'), ''),
            'to': next((h['value'] for h in headers if h['name'] == 'To'), ''),
            'subject': next((h['value'] for h in headers if h['name'] == 'Subject'), ''),
            'date': next((h['value'] for h in headers if h['name'] == 'Date'), ''),
            'body': body
        }
        
        return email_data
    except Exception as e:
        logger.error(f"Error getting email content: {str(e)}")
        raise

def search_emails(service, query, max_results=10):
    """
    Search emails using Gmail's search syntax.
    """
    try:
        results = service.users().messages().list(
            userId='me',
            maxResults=max_results,
            q=query
        ).execute()
        
        messages = results.get('messages', [])
        emails = []
        
        for msg in messages:
            email = service.users().messages().get(
                userId='me',
                id=msg['id'],
                format='metadata',
                metadataHeaders=['From', 'Subject', 'Date']
            ).execute()
            
            headers = email['payload']['headers']
            email_data = {
                'id': email['id'],
                'threadId': email['threadId'],
                'from': next((h['value'] for h in headers if h['name'] == 'From'), ''),
                'subject': next((h['value'] for h in headers if h['name'] == 'Subject'), ''),
                'date': next((h['value'] for h in headers if h['name'] == 'Date'), '')
            }
            emails.append(email_data)
            
        return emails
    except Exception as e:
        logger.error(f"Error searching emails: {str(e)}")
        raise 