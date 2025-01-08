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

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()

# Initialize the Flask application
app = Flask(__name__)
CORS(app, origins=['*'])
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')  # Make sure this is secure in production

# Initialize OpenAI client
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY")
)

# Update the system prompt to be more specific about calendar creation
system_prompt = '''You are an AI assistant with the following capabilities:
1. Implementing calendar events - You can view and create calendar events
2. Managing Gmail - You can read, send, and modify emails
3. Helping schedule appointments
4. Managing homework and test schedules
5. Creating and managing todo lists
6. Answering questions informatively
7. You can use Google Calendar API to add events to users calendar
8. You can use Gmail API to manage emails

When handling email-related requests:
- For viewing emails, use the Gmail API endpoints
- For sending emails, collect all necessary details (recipient, subject, body)
- For modifying emails, ensure user confirmation before making changes

When handling calendar-related requests:
- For viewing events, use the calendar API endpoints
- For creating events, collect all necessary details and use this format:
  {
    "action": "create_event",
    "event_details": {
      "summary": "Event title",
      "description": "Event description",
      "start_time": "YYYY-MM-DDTHH:MM:SS",
      "end_time": "YYYY-MM-DDTHH:MM:SS",
      "timezone": "User's timezone"
    }
  }

Always confirm event details with the user before creating them. When a user wants to create an event, ask for any missing information and format the response as shown above.

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

    return build('calendar', 'v3', credentials=credentials)

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

    return build('gmail', 'v1', credentials=credentials)

# Add these constants after the existing imports
SCOPES = [
    'https://www.googleapis.com/auth/calendar',  # Full calendar access
    'https://www.googleapis.com/auth/gmail.modify',  # Read, modify, and send emails
    'https://www.googleapis.com/auth/gmail.send',    # Send emails
    'https://www.googleapis.com/auth/gmail.compose'  # Create emails
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

# Add this new function to handle sending emails
def send_email(service, to, subject, body):
    try:
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        import base64
        
        # Create MIMEMultipart message
        message = MIMEMultipart()
        message['to'] = to
        message['subject'] = subject
        
        # Add body as MIMEText
        msg = MIMEText(body)
        message.attach(msg)
        
        # Create the raw email
        raw_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode('utf-8')
        
        try:
            # Send the email
            sent_message = service.users().messages().send(
                userId='me',
                body={'raw': raw_message}
            ).execute()
            logger.info(f"Email sent successfully. Message Id: {sent_message['id']}")
            return sent_message
        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            raise e
            
    except Exception as e:
        logger.error(f"Error creating email: {str(e)}")
        raise e

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        user_message = data.get('message')
        conversation_id = data.get('conversation_id', 'default')
        
        if not user_message:
            return jsonify({"error": "No message provided"}), 400

        # Load conversation history
        conversation_history = load_conversation_history()
        
        # Initialize conversation if it doesn't exist
        if conversation_id not in conversation_history:
            conversation_history[conversation_id] = []
        
        # Get both calendar and Gmail context
        calendar_context = ""
        gmail_context = ""
        
        try:
            # Get Gmail context
            gmail_service = get_gmail_service()
            results = gmail_service.users().messages().list(
                userId='me',
                maxResults=5,
                labelIds=['INBOX']
            ).execute()
            
            messages = results.get('messages', [])
            if messages:
                gmail_context = "\nYour recent emails:\n"
                for msg in messages:
                    message = gmail_service.users().messages().get(
                        userId='me',
                        id=msg['id'],
                        format='metadata',
                        metadataHeaders=['Subject', 'From']
                    ).execute()
                    
                    headers = message['payload']['headers']
                    subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                    sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown Sender')
                    gmail_context += f"- From: {sender}, Subject: {subject}\n"
            
            # Get calendar context (existing code)
            service = get_calendar_service()
            now = datetime.now().isoformat() + 'Z'
            events_result = service.events().list(
                calendarId='primary',
                timeMin=now,
                maxResults=5,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])
            
            if events:
                calendar_context = "\nYour upcoming calendar events:\n"
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    summary = event.get('summary', 'Untitled Event')
                    calendar_context += f"- {summary} (starts at {start})\n"
            
        except Exception as e:
            if "No valid credentials available" in str(e):
                return jsonify({
                    "error": "Authorization required",
                    "authorization_url": f"http://localhost:5500/calendar/authorize"
                }), 401
            logger.error(f"Error getting context: {e}")
            calendar_context = "\nCalendar and Gmail integration currently unavailable.\n"

        # Construct messages including conversation history and both contexts
        messages = [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "system",
                "content": f"Current context:{calendar_context}{gmail_context}"
            }
        ]
        
        # Add conversation history
        messages.extend(conversation_history[conversation_id])
        
        # Add current user message
        messages.append({
            "role": "user",
            "content": user_message
        })

        # Get AI response
        chat_completion = client.chat.completions.create(
            messages=messages,
            model="gpt-4o-mini"
        )

        ai_response = chat_completion.choices[0].message.content

        # Check if the response contains a calendar event creation request
        try:
            # Try to parse the response as JSON if it contains event details
            if "action" in ai_response and "create_event" in ai_response:
                # Extract the JSON part from the response
                json_str = ai_response[ai_response.find("{"):ai_response.rfind("}")+1]
                event_data = json.loads(json_str)
                
                if event_data.get("action") == "create_event":
                    # Create the calendar event
                    service = get_calendar_service()
                    event_details = event_data.get("event_details", {})
                    
                    event = {
                        'summary': event_details.get('summary'),
                        'description': event_details.get('description'),
                        'start': {
                            'dateTime': event_details.get('start_time'),
                            'timeZone': event_details.get('timezone', 'UTC'),
                        },
                        'end': {
                            'dateTime': event_details.get('end_time'),
                            'timeZone': event_details.get('timezone', 'UTC'),
                        },
                    }

                    created_event = service.events().insert(calendarId='primary', body=event).execute()
                    ai_response += f"\n\nEvent has been created successfully! Event ID: {created_event.get('id')}"
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.debug(f"No valid event creation data found in response: {e}")
            # Not a calendar event creation response, continue normally

        # Check if the response indicates an email should be sent
        if any(word in user_message.lower() for word in ['send email', 'send an email', 'send a message']):
            try:
                gmail_service = get_gmail_service()
                
                # Extract email details from the message
                message_lower = user_message.lower()
                
                # Extract recipient
                to_email = None
                if "to:" in message_lower:
                    to_email = message_lower.split("to:")[1].split()[0].strip()
                elif "to " in message_lower:
                    to_email = message_lower.split("to ")[1].split()[0].strip()
                
                # Extract subject
                subject = "Message from AI Assistant"
                if "subject:" in message_lower:
                    subject = user_message.split("subject:")[1].split("body:")[0].strip()
                
                # Extract body
                body = "No body provided"
                if "body:" in message_lower:
                    body = user_message.split("body:")[1].strip()
                elif "saying" in message_lower:
                    body = user_message.split("saying")[1].strip()
                elif "message:" in message_lower:
                    body = user_message.split("message:")[1].strip()
                
                if to_email:
                    # Send the email
                    send_email(gmail_service, to_email, subject, body)
                    ai_response += f"\n\nEmail sent successfully!\nTo: {to_email}\nSubject: {subject}\nBody: {body}"
                else:
                    ai_response += "\n\nI couldn't determine the email recipient. Please specify using 'to: email@example.com'"
                    
            except Exception as e:
                logger.error(f"Email sending error: {str(e)}")
                ai_response += f"\n\nFailed to send email: {str(e)}"

        # Save the conversation
        conversation_history[conversation_id].append({
            "role": "user",
            "content": user_message
        })
        conversation_history[conversation_id].append({
            "role": "assistant",
            "content": ai_response
        })
        
        # Trim history if it gets too long
        if len(conversation_history[conversation_id]) > 20:
            conversation_history[conversation_id] = conversation_history[conversation_id][-20:]
        
        # Save updated history
        save_conversation_history(conversation_history)

        return jsonify({
            "response": ai_response,
            "conversation_id": conversation_id
        })

    except Exception as e:
        logger.error(f"Chat error: {e}")
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

# Run the application
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5500, debug=True) 