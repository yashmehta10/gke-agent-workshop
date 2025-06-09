FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

# Make port 8080 available to the world outside this container
EXPOSE 8080
CMD ["gunicorn", "--workers", "1", "--bind", "0.0.0.0:8080", "main:app"]