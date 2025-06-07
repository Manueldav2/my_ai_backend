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
import pickle
import logging
from typing import List, Dict
import json
import base64

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

# Initialize the Flask application
app = Flask(__name__)
# Update CORS configuration to allow requests from your deployed frontend
CORS(app, resources={
    r"/*": {
        "origins": ["https://myai-chatbot.web.app", "http://localhost:3000"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type"],
        "supports_credentials": True
    }
})
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')  # Make sure this is secure in production

# Store user credentials in memory (consider using Redis in production)
user_credentials = {}

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

def get_user_credentials(user_id):
    """Get user credentials from memory"""
    if user_id not in user_credentials:
        raise Exception("User credentials not found")
        
    credentials = user_credentials[user_id]
    
    # Refresh token if expired
    if credentials.expired:
        try:
            credentials.refresh(Request())
            user_credentials[user_id] = credentials  # Store refreshed credentials
        except Exception as e:
            logger.error(f"Error refreshing credentials: {str(e)}")
            raise Exception("Failed to refresh credentials")
            
    return credentials

def get_calendar_service(user_id):
    """Gets calendar service using user-specific credentials"""
    try:
        credentials = get_user_credentials(user_id)
        return build('calendar', 'v3', credentials=credentials, cache_discovery=False)
    except Exception as e:
        logger.error(f"Error getting calendar service: {str(e)}")
        raise

def get_gmail_service(user_id):
    """Gets Gmail service using user-specific credentials"""
    try:
        credentials = get_user_credentials(user_id)
        return build('gmail', 'v1', credentials=credentials, cache_discovery=False)
    except Exception as e:
        logger.error(f"Error getting Gmail service: {str(e)}")
        raise

# Initialize OpenAI client
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

# Update the system prompt to better handle calendar requests
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

# Add a helper function to handle calendar operations
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

# Add this helper function near the other helper functions
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

# Update the send_email function with more direct Gmail API usage
def send_email(service, to, subject, body):
    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        logger.info("=== Starting email send process ===")
        logger.info(f"To: {to}")
        logger.info(f"Subject: {subject}")
        logger.info(f"Body: {body}")
        
        # Create message container
        message = MIMEMultipart()
        message['to'] = to
        message['subject'] = subject
        
        # Create the body
        msg = MIMEText(body)
        message.attach(msg)
        
        # Encode the message
        raw = base64.urlsafe_b64encode(message.as_bytes())
        raw = raw.decode()
        logger.info("Message encoded successfully")
        
        # Create the final message
        message_body = {'raw': raw}
        logger.info("Message body prepared")
        
        # Send the message using the simpler approach
        logger.info("Attempting to send message through Gmail API...")
        sent_message = service.users().messages().send(
            userId='me',
            body=message_body
        ).execute()
        
        logger.info(f"Message sent successfully! Message ID: {sent_message['id']}")
        return True
            
    except Exception as e:
        logger.error(f"Error in send_email: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Error details: {e.__dict__}")
        raise e

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        message = data.get('message')
        user_id = data.get('user_id')
        
        if not message or not user_id:
            return jsonify({"error": "Message and user_id are required"}), 400

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

        # Create messages for OpenAI
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add context about calendar
        messages.append({"role": "system", "content": calendar_context})
        
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

# Update the calendar event creation function
def create_calendar_event(service, event_details):
    """Create a calendar event using the Google Calendar API."""
    try:
        logger.info("=== Creating Calendar Event ===")
        logger.info(f"Event details received: {event_details}")
        
        # Extract event details
        summary = event_details.get('summary', 'Untitled Event')
        description = event_details.get('description', '')
        location = event_details.get('location', '')
        start_time = event_details.get('start_time')
        end_time = event_details.get('end_time')
        attendees = event_details.get('attendees', [])
        recurrence = event_details.get('recurrence')
        timezone = event_details.get('timezone', 'America/New_York')  # Default to NY timezone
        
        logger.info(f"Extracted details - Summary: {summary}")
        logger.info(f"Start time: {start_time}")
        logger.info(f"End time: {end_time}")
        logger.info(f"Location: {location}")
        logger.info(f"Attendees: {attendees}")
        logger.info(f"Recurrence: {recurrence}")
        logger.info(f"Timezone: {timezone}")
        
        # Create the event object with the times as provided
        event = {
            'summary': summary,
            'description': description,
            'location': location,
            'start': {
                'dateTime': start_time,
                'timeZone': timezone,
            },
            'end': {
                'dateTime': end_time,
                'timeZone': timezone,
            },
            'attendees': [{'email': email} for email in attendees] if attendees else [],
        }
        
        # Add recurrence if specified
        if recurrence:
            logger.info("Adding recurrence rule")
            event['recurrence'] = [recurrence]
        
        logger.info("Attempting to insert event into calendar...")
        event = service.events().insert(calendarId='primary', body=event).execute()
        logger.info(f"Event created successfully with ID: {event.get('id')}")
        return event
        
    except Exception as e:
        logger.error(f"Error creating calendar event: {e}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Error details: {e.__dict__}")
        raise

# Run the application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5500, debug=True) 