import firebase_admin
from firebase_admin import credentials, firestore, storage
from dotenv import load_dotenv
import os
import json

load_dotenv()

def initialize_firebase():
    try:
        # Try to get default app if it exists
        return firebase_admin.get_app()
    except ValueError:
        # Initialize app if it doesn't exist
        cred = credentials.Certificate('firebase_creds.json')
        # cred = credentials.Certificate(json.loads(os.getenv('FIREBASE_CREDS')))
        app = firebase_admin.initialize_app(credential=cred, options={
            'storageBucket': 'embedai-91f62.appspot.com'
        })
        return app

# Initialize Firebase and get Firestore client
firebase_app = initialize_firebase()
db = firestore.client()

def get_storage_info():
    try:
        # List all buckets
        buckets = storage.list_buckets()
        print("Available buckets:")
        for bucket in buckets:
            print(f"Bucket: {bucket.name}")
            print(f"Location: {bucket.location}")
            print(f"Storage class: {bucket.storage_class}")
            print("---")
        
        # Get default bucket
        default_bucket = storage.bucket()
        print(f"\nDefault bucket: {default_bucket.name}")
        
    except Exception as e:
        print(f"Error getting storage info: {str(e)}")

# Add this line at the bottom if you want to check bucket info during initialization
# get_storage_info()