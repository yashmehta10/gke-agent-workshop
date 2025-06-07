from flask import Flask
from datetime import datetime
import os

app = Flask(__name__)

@app.route('/')
def hello():
    
    return f"<h1>Hello World from GKE Deployment!</h1><p>Time: {datetime.now()}</p>"

if __name__ == '__main__':
    # This is for local Flask development server. Gunicorn is used in the container.
    app.run(host='0.0.0.0', port=8080)
