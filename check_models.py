import requests, os
from dotenv import load_dotenv
load_dotenv()

key = os.getenv('GEMINI_API_KEY')
r = requests.get(f'https://generativelanguage.googleapis.com/v1beta/models?key={key}')
models = r.json()
for m in models.get('models', []):
    print(m['name'])