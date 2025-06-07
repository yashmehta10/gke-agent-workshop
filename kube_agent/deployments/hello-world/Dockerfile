# Dockerfile

# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces layer size. --trusted-host is good practice for CI/CD environments.
RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

# Copy the rest of the application's code into the container at /app
COPY . .

# Make port 8080 available to the world outside this container
EXPOSE 8080

# Define the command to run your app using gunicorn
# This is a production-ready WSGI server.
# It runs 1 worker, binds to all interfaces on port 8080.
CMD ["gunicorn", "--workers", "1", "--bind", "0.0.0.0:8080", "main:app"]

# --- requirements.txt ---
# Flask==2.3.2
# gunicorn==21.2.0


# --- main.py ---
# from flask import Flask
# import os

# app = Flask(__name__)

# @app.route('/')
# def hello():
#     # Example of reading an environment variable
#     info_var = os.environ.get('INFO_VAR', 'Not set')
#     return f"<h1>Hello World from GKE Deployment!</h1><p>INFO_VAR: {info_var}</p>"

# if __name__ == '__main__':
#     # This is for local Flask development server. Gunicorn is used in the container.
#     app.run(host='0.0.0.0', port=8080)

