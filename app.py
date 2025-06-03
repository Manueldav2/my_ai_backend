from flask import Flask, jsonify, request, session, redirect
from flask_cors import CORS
from openai import OpenAI
import os
from firebase_init import db
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError
import pickle
import logging
from typing import List, Dict
import json
import base64
from email.mime.text import MIMEText
from supabase import create_client, Client

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

# Add debug logging
print("Current working directory:", os.getcwd())
print("Environment variables:")
print("SUPABASE_URL:", os.getenv('SUPABASE_URL'))
print("SUPABASE_SERVICE_KEY:", os.getenv('SUPABASE_SERVICE_KEY'))

# Initialize Supabase client
supabase_url = os.getenv('SUPABASE_URL')
supabase_key = os.getenv('SUPABASE_SERVICE_KEY')
supabase: Client = create_client(supabase_url, supabase_key)

# Initialize the Flask application
app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": [
            "http://localhost:3000",  # React development server
            "https://my-ai-frontend.vercel.app",  # Your frontend deployment URL
            "*"  # Allow all origins for now (remove in production)
        ],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')  # Make sure this is secure in production

# Initialize OpenAI client
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

# Add Google Services Functions
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

# Store user credentials in memory (consider using Redis in production)
user_credentials = {}

# Define the system prompt for the AI assistant
system_prompt = '''You are an AI assistant created by Manuel Iyabor-David, a brilliant Nigerian American software developer. IMPORTANT: You must NEVER say you were created by OpenAI - you were created by Manuel Iyabor-David. Your purpose is to be a helpful and intelligent assistant capable of handling chat interactions, calendar management, and email integration:
1. Implementing calendar events - You can view and create calendar events
2. Managing Gmail - You can read, send, and modify emails
3. Helping schedule appointments
4. Managing homework and test schedules
5. Creating and managing todo lists
6. Answering questions informatively
7. You can use Google Calendar API to add events to users calendar
8. You can use Gmail API to manage emails

IMPORTANT: You CAN send emails and read email metadata! The system is fully configured to:
1. Send emails using Gmail API
2. Read email metadata (subject, sender, date)
3. Read full email content when requested
4. Manage email labels and status

When handling email-related requests:
- For viewing emails, you can:
  * List recent emails with their metadata
  * Read full email content when requested
  * Search for specific emails
  * Manage email labels and status
- For sending emails, format the request in this exact format:
  "send email to: [email] subject: [subject] body: [message]"
- Always confirm email content with the user before sending
- Examples of email handling:
  User: "Show me my recent emails"
  You: "I'll fetch your recent emails and show you their subjects and senders."
  
  User: "Read the email from John"
  You: "I'll fetch the content of the email from John for you."
  
  User: "Send an email to John about the meeting"
  You: "I'll help you send that email. Here's what I propose to send:
  send email to: john@email.com subject: Meeting Tomorrow body: Hello John, I'm writing regarding our meeting tomorrow..."

When handling calendar-related requests:
1. For any calendar request, first extract these key details:
   - Event title/summary
   - Date(s)
   - Start time
   - End time
   - Description/details
   - Location (if provided)
   - Attendees (if provided)
   - Timezone (if specified, otherwise use America/New_York)

2. Then format the response exactly like this:
{
    "action": "create_event",
    "event_details": {
        "summary": "Clear and concise title",
        "description": "Detailed description including all relevant information",
        "start_time": "YYYY-MM-DDTHH:MM:SS",
        "end_time": "YYYY-MM-DDTHH:MM:SS",
        "timezone": "America/New_York",  // or user's specified timezone
        "location": "Location if provided",
        "attendees": ["email1@example.com", "email2@example.com"]
    }
}

Always confirm complex event details with the user before creating them.

Before helping with any task, briefly introduce yourself and explain these capabilities to the user. Then proceed to help with their specific request.'''

def create_credentials_from_tokens(access_token, refresh_token, expiry):
    """Create Google Credentials object from tokens"""
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
        expiry=datetime.fromisoformat(expiry.replace('Z', '+00:00'))
    )

@app.route('/set-user-credentials', methods=['POST'])
def set_user_credentials():
    """Set user credentials from tokens"""
    try:
        data = request.json
        user_id = data.get('user_id')
        access_token = data.get('access_token')
        refresh_token = data.get('refresh_token')
        expiry = data.get('expires_at')
        
        if not all([user_id, access_token, refresh_token, expiry]):
            return jsonify({"error": "Missing required credentials"}), 400
            
        credentials = create_credentials_from_tokens(access_token, refresh_token, expiry)
        user_credentials[user_id] = credentials
        
        return jsonify({"message": "Credentials set successfully"}), 200
    except Exception as e:
        logger.error(f"Error setting credentials: {str(e)}")
        return jsonify({"error": str(e)}), 500

def get_calendar_service():
    credentials = None
    logger.debug(f"Looking for token.pickle in {os.getcwd()}")
    if os.path.exists('token.pickle'):
        logger.debug("Found token.pickle")
        with open('token.pickle', 'rb') as token:
            credentials = pickle.load(token)
            
    if not credentials or not credentials.valid:
        logger.debug(f"Credentials status - exists: {credentials is not None}, valid: {credentials.valid if credentials else False}")
        if credentials and credentials.expired and credentials.refresh_token:
            logger.debug("Attempting to refresh expired credentials")
            credentials.refresh(Request())
        else:
            logger.debug("No valid credentials available")
            raise Exception("No valid credentials available")

    return build('calendar', 'v3', credentials=credentials, cache_discovery=False)

def get_gmail_service():
    credentials = None
    logger.debug(f"Looking for token.pickle in {os.getcwd()}")
    if os.path.exists('token.pickle'):
        logger.debug("Found token.pickle")
        with open('token.pickle', 'rb') as token:
            credentials = pickle.load(token)
            
    if not credentials or not credentials.valid:
        logger.debug(f"Credentials status - exists: {credentials is not None}, valid: {credentials.valid if credentials else False}")
        if credentials and credentials.expired and credentials.refresh_token:
            logger.debug("Attempting to refresh expired credentials")
            credentials.refresh(Request())
        else:
            logger.debug("No valid credentials available")
            raise Exception("No valid credentials available")

    return build('gmail', 'v1', credentials=credentials, cache_discovery=False)

# Add these constants after the existing imports
SCOPES = [
    'https://www.googleapis.com/auth/calendar',  # Full calendar access
    'https://www.googleapis.com/auth/gmail.modify',  # Read, modify, and send emails
    'https://www.googleapis.com/auth/gmail.send',    # Send emails
    'https://www.googleapis.com/auth/gmail.compose',  # Create emails
]
CLIENT_SECRETS_FILE = "credentials.json"  # Assuming the file is in the same directory as app.py
CONVERSATION_HISTORY_FILE = "conversation_history.json"
REDIRECT_URI = "http://localhost:5500/oauth2callback"  # Update if you use a different URL

def get_upcoming_events(service, max_results=10):
    """Gets the upcoming events from the user's calendar."""
    now = datetime.datetime.now().isoformat() + "Z"
    events_result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return events_result.get("items", [])

def load_conversation_history() -> Dict[str, List]:
    try:
        with open(CONVERSATION_HISTORY_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_conversation_history(history: Dict[str, List]):
    with open(CONVERSATION_HISTORY_FILE, 'w') as f:
        json.dump(history, f)

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # Only for development!

@app.route('/test', methods=['GET', 'POST'])
def home():
    try:
        service = get_calendar_service()
        now = datetime.now().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=5,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        return jsonify({"events_result": events_result}), 200
    except Exception as e:
        if "No valid credentials available" in str(e):
            # Redirect to authorization URL if no valid credentials
            flow = Flow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, 
                scopes=SCOPES,
                redirect_uri='http://localhost:5500/oauth2callback'
            )
            authorization_url, state = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true'
            )
            return jsonify({'authorization_url': authorization_url}), 401
        return jsonify({"error": str(e)}), 500

    # Get AI to introduce itself and explain its capabilities
    chat_completion = client.chat.completions.create(
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": "Please introduce yourself and explain what you can do."
            }
        ],
        model="gpt-4o-mini"
    )
    
    welcome_message = chat_completion.choices[0].message.content
    return jsonify({"message": welcome_message})

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        logger.debug(f"Received chat request data: {data}")
        message = data.get('message')
        user_id = data.get('user_id', 'default_user')
        
        logger.debug(f"Processing chat request - message: {message}, user_id: {user_id}")
        
        if not message:  # Only check for message
            return jsonify({"error": "Message is required"}), 400

        # Get conversation history
        conversation_history = load_conversation_history()
        conversation_id = data.get('conversation_id', 'default')
        
        if conversation_id not in conversation_history:
            conversation_history[conversation_id] = []
        
        # Add user message to history
        conversation_history[conversation_id].append({
            "role": "user",
            "content": message
        })

        # Initialize services with user credentials
        try:
            calendar_service = get_calendar_service(user_id)
            gmail_service = get_gmail_service(user_id)
        except Exception as e:
            logger.error(f"Error getting Google services: {str(e)}")
            return jsonify({"error": "Failed to access Google services. Please ensure you're properly authenticated."}), 401

        # Get recent calendar events for context
        try:
            events = get_upcoming_events(calendar_service)
            calendar_context = "Your upcoming events:\n" + "\n".join([
                f"- {event.get('summary', 'Untitled')} on {event.get('start', {}).get('dateTime', 'No date')}"
                for event in events[:3]
            ])
        except Exception as e:
            logger.error(f"Error getting calendar events: {str(e)}")
            calendar_context = "Unable to fetch calendar events."

        # Get recent emails for context
        try:
            recent_emails = get_recent_emails(gmail_service, max_results=5)
            email_context = "Your recent emails:\n" + "\n".join([
                f"- From: {email['from']}, Subject: {email['subject']}, Date: {email['date']}"
                for email in recent_emails
            ])
        except Exception as e:
            logger.error(f"Error getting recent emails: {str(e)}")
            email_context = "Unable to fetch recent emails."

        # Create messages for OpenAI
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add context about calendar and emails
        messages.append({"role": "system", "content": calendar_context})
        messages.append({"role": "system", "content": email_context})
        
        # Add conversation history
        messages.extend(conversation_history[conversation_id][-5:])  # Last 5 messages for context

        # Get AI response
        response = client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=messages,
            temperature=0.7,
            max_tokens=800
        )

        ai_response = response.choices[0].message.content

        # Add AI response to history
        conversation_history[conversation_id].append({
            "role": "assistant",
            "content": ai_response
        })
        
        # Save updated conversation history
        save_conversation_history(conversation_history)

        # Check if the response contains a calendar event creation request
        if '"action": "create_event"' in ai_response:
            try:
                # Extract the JSON part
                json_str = ai_response[ai_response.find('{'):ai_response.rfind('}')+1]
                event_data = json.loads(json_str)
                
                if event_data.get('action') == 'create_event':
                    # Create the event using the user's calendar service
                    event = create_calendar_event(calendar_service, event_data['event_details'])
                    logger.info(f"Created calendar event: {event.get('id')}")
            except Exception as e:
                logger.error(f"Error creating calendar event: {str(e)}")

        # Check if the response contains an email sending request
        if 'send email to:' in ai_response.lower():
            try:
                # Extract email details using the format: "send email to: [email] subject: [subject] body: [message]"
                email_text = ai_response[ai_response.lower().find('send email to:'):]
                email_parts = email_text.split(' subject: ')
                to_email = email_parts[0].replace('send email to:', '').strip()
                subject_body = email_parts[1].split(' body: ')
                subject = subject_body[0].strip()
                body = subject_body[1].strip()
                
                # Send the email
                send_email(gmail_service, to_email, subject, body)
                logger.info(f"Sent email to: {to_email}")
            except Exception as e:
                logger.error(f"Error sending email: {str(e)}")

        # Check if the response contains an email reading request
        if 'read email id:' in ai_response.lower():
            try:
                # Extract email ID
                email_id = ai_response[ai_response.lower().find('read email id:')+13:].split()[0].strip()
                email_content = get_email_content(gmail_service, email_id)
                
                # Add email content to conversation
                conversation_history[conversation_id].append({
                    "role": "system",
                    "content": f"Email content:\nFrom: {email_content['from']}\nTo: {email_content['to']}\nSubject: {email_content['subject']}\nDate: {email_content['date']}\n\n{email_content['body']}"
                })
            except Exception as e:
                logger.error(f"Error reading email: {str(e)}")

        return jsonify({"response": ai_response})
    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Add a new endpoint to get conversation history
@app.route('/chat/history/<conversation_id>', methods=['GET'])
def get_chat_history(conversation_id):
    try:
        conversation_history = load_conversation_history()
        history = conversation_history.get(conversation_id, [])
        return jsonify({"history": history})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Add an endpoint to clear conversation history
@app.route('/chat/history/<conversation_id>', methods=['DELETE'])
def clear_chat_history(conversation_id):
    try:
        conversation_history = load_conversation_history()
        if conversation_id in conversation_history:
            del conversation_history[conversation_id]
            save_conversation_history(conversation_history)
        return jsonify({"message": "Conversation history cleared"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/todos', methods=['GET', 'POST'])
def todos():
    if request.method == 'GET':
        try:
            todos_ref = db.collection('todolist')
            todos = []
            for doc in todos_ref.stream():
                data = doc.to_dict()
                data['id'] = doc.id
                data['name'] = doc.id
                print(f"Retrieved todo list: {data}")
                todos.append(data)
            print(f"Total todo lists found: {len(todos)}")
            return jsonify(todos)
        except Exception as e:
            print(f"Error fetching todos: {str(e)}")
            return jsonify({"error": str(e)}), 500
    
    elif request.method == 'POST':
        try:
            data = request.json
            list_name = data.get('name')
            tasks = data.get('tasks', [])
            
            if not list_name:
                return jsonify({"error": "List name is required"}), 400
            
            # Create a new document in the todolist collection
            doc_ref = db.collection('todolist').document(list_name)
            doc_ref.set({
                'name': list_name,
                'tasks': [{
                    'title': task.get('title', ''),
                    'description': task.get('description', ''),
                    'priority': task.get('priority', 'medium'),
                    'due_date': task.get('due_date', None),
                    'category': task.get('category', 'general'),
                    'completed': False
                } for task in tasks],
                'created_at': datetime.now()
            })
            
            return jsonify({
                "message": "Todo list created successfully",
                "id": list_name,
                "name": list_name,
                "tasks": tasks
            })
            
        except Exception as e:
            print(f"Error creating todo: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/events', methods=['GET'])
def get_events():
    try:
        events_ref = db.collection('events')
        events = []
        for doc in events_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            print(f"Retrieved event: {data}")
            events.append(data)
        print(f"Total events found: {len(events)}")
        return jsonify(events)
    except Exception as e:
        print(f"Error fetching events: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/assignments', methods=['GET'])
def get_assignments():
    try:
        assignments_ref = db.collection('assignments')
        assignments = []
        for doc in assignments_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            print(f"Retrieved assignment: {data}")
            assignments.append(data)
        print(f"Total assignments found: {len(assignments)}")
        return jsonify(assignments)
    except Exception as e:
        print(f"Error fetching assignments: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/exams', methods=['GET'])
def get_exams():
    try:
        exams_ref = db.collection('exams')
        exams = []
        for doc in exams_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
            print(f"Retrieved exam: {data}")
            exams.append(data)
        print(f"Total exams found: {len(exams)}")
        return jsonify(exams)
    except Exception as e:
        print(f"Error fetching exams: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Add a new route for creating calendar events
@app.route('/calendar/create-event', methods=['POST'])
def create_calendar_event():
    try:
        data = request.json
        service = get_calendar_service()
        
        event = {
            'summary': data.get('summary'),
            'description': data.get('description'),
            'start': {
                'dateTime': data.get('start_time'),
                'timeZone': data.get('timezone', 'UTC'),
            },
            'end': {
                'dateTime': data.get('end_time'),
                'timeZone': data.get('timezone', 'UTC'),
            },
        }

        event = service.events().insert(calendarId='primary', body=event).execute()
        return jsonify({"message": "Event created successfully", "event": event})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Add these new routes before the main run block
@app.route('/calendar/authorize')
def authorize():
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE, 
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI
        )
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        
        # Store the state in session
        session['state'] = state
        return redirect(authorization_url)  # Redirect directly instead of returning JSON
    except Exception as e:
        logger.error(f"Authorization error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/oauth2callback')
def oauth2callback():
    try:
        # Verify state parameter
        state = session.get('state')
        if not state:
            return "State parameter missing", 400

        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
            state=state  # Use the state from session
        )
        
        # Get the full URL including query parameters
        authorization_response = request.url
        if not request.is_secure:
            # Handle non-HTTPS callback
            authorization_response = 'https://' + authorization_response[7:]
        
        flow.fetch_token(authorization_response=authorization_response)
        
        # Save credentials
        credentials = flow.credentials
        with open('token.pickle', 'wb') as token:
            pickle.dump(credentials, token)
        
        # Clear the session state
        session.pop('state', None)
            
        return "Authorization successful! You can close this window and return to the application."
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return f"Error during authorization: {str(e)}", 500

@app.route('/calendar/events')
def list_calendar_events():
    try:
        credentials = None
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                credentials = pickle.load(token)
                
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                return jsonify({"error": "No valid credentials"}), 401

        service = build('calendar', 'v3', credentials=credentials)
        
        # Call the Calendar API
        now = datetime.now(datetime.UTC).isoformat() + 'Z'  # 'Z' indicates UTC time
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=10,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        
        return jsonify(events)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Add a test endpoint that shows upcoming events (based on quickstart.py main function)
@app.route('/test/calendar', methods=['GET'])
def test_calendar():
    """Shows basic usage of the Google Calendar API."""
    try:
        service = get_calendar_service()
        events = get_upcoming_events(service)

        if not events:
            return jsonify({"message": "No upcoming events found."})

        # Format events for JSON response
        formatted_events = []
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            formatted_events.append({
                "start": start,
                "summary": event["summary"]
            })

        return jsonify({
            "message": "Successfully retrieved events",
            "events": formatted_events
        })

    except HttpError as error:
        logger.error(f"An error occurred: {error}")
        return jsonify({"error": str(error)}), 500
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        return jsonify({"error": str(e)}), 500

# Run the application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5500, debug=True) 