from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "OK Arcade"

@app.route('/test')
def test():
    return "Working!"

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
from flask import Flask
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "OK Arcade"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8000))
    print(f"Starting on port {port}")
    app.run(host='0.0.0.0', port=port)
