# GKE Agent Workshop

This project demonstrates an AI-powered agent capable of managing Google Kubernetes Engine (GKE) clusters and Docker images. The agent leverages the Google Agent Development Kit (ADK) and custom tools to interact with Google Cloud services.

## Overview

The `kube_agent` is designed to simplify GKE operations by allowing users to interact with their cluster using natural language. It can perform tasks such as:

*   Listing existing GKE jobs and their statuses.
*   Running new jobs on GKE from container images.
*   Creating and managing GKE deployments.
*   Building Docker images from local contexts and pushing them to Google Artifact Registry.

This workshop provides the necessary scripts and code to set up the environment, build the agent, and interact with it.

## Features

*   **GKE Job Management**: List jobs, run new containerized jobs.
*   **GKE Deployment Management**: Create deployments, get deployment status, list deployments.
*   **Docker Image Management**: Build Docker images for specific platforms and push them to Google Artifact Registry.
*   **Natural Language Interaction**: Powered by a Gemini model via the Google ADK.
*   **Extensible Toolset**: Easily add more tools to expand the agent's capabilities.

## Prerequisites

Before you begin, ensure you have the following installed and configured:

1.  **Google Cloud SDK (`gcloud`)**: Installation Guide
2.  **Python 3.9+**: Python Downloads
3.  **Docker**: Docker Installation Guide
4.  **Git**: Git Downloads
5.  A **Google Cloud Project** with billing enabled.

## Setup

Follow these steps to set up the project environment:

### 1. Clone the Repository

```bash
git clone https://github.com/yashmehta10/gke-agent-workshop.git
cd gke-agent-workshop
```

### 2. Google Cloud Project Setup

The `setup.sh` script automates the Google Cloud environment preparation. It will:
*   Prompt for your Google Cloud Project ID.
*   Enable necessary APIs (Kubernetes Engine, Artifact Registry, Cloud Build, Vertex AI).
*   Create a GKE Autopilot cluster (default: `autopilot-cluster-1` in `us-central1`).
*   Create an Artifact Registry Docker repository (default: `ai-docker-repo` in `us-central1`).
*   Configure Docker to authenticate with Artifact Registry.

Run the script:

```bash
chmod +x setup.sh
./setup.sh
```
Follow the prompts. This step might take several minutes, especially for GKE cluster creation.

### 3. Python Environment Setup

It's recommended to use a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Environment Variable Configuration

The agent and its tools rely on environment variables for configuration.

*   **Main Configuration (`kube_agent/.env`)**:
    This file contains default settings for connecting to your Google Cloud project and GKE cluster. The `setup.sh` script creates resources with specific names. If you used different names or regions during the manual setup or modified `setup.sh`, update this file accordingly.

    ```env
    GOOGLE_GENAI_USE_VERTEXAI=TRUE
    GOOGLE_CLOUD_PROJECT=your-gcp-project-id # Should match the project used in setup.sh
    GOOGLE_CLOUD_LOCATION=us-central1       # Should match the region used for GKE/AR
    GKE_CLUSTER_NAME=autopilot-cluster-1    # Should match the GKE cluster name
    TEST_AR_REPOSITORY_NAME=ai-docker-repo  # Should match the AR repo name
    TEST_AR_IMAGE_NAME=hello-world-amd64    # Default image name for testing docker_tools
    GKE_LOCATION=us-central1                # Should match the GKE cluster region
    ```
    **Important**: Replace `your-gcp-project-id` with your actual Google Cloud Project ID if it's not already set by a previous step or if you didn't use `gcloud config set project`.

*   **Local Development Overrides (`kube_agent/.env.local`)**:
    This file is for local development and overrides. It's typically not checked into version control. You can use it to point to local resources or disable certain cloud features for local testing if needed.

    ```env
    GOOGLE_GENAI_USE_VERTEXAI=
    GOOGLE_CLOUD_PROJECT=
    GOOGLE_CLOUD_LOCATION=
    # Example: TIMESHEET_DB_PATH=timesheet_agent/database/timesheet.db (if you had other agents)
    ```

## Directory Structure

```
gke-agent-workshop/
├── kube_agent/
│   ├── .env                    # Main environment variables for the agent
│   ├── .env.local              # Local overrides for environment variables (ignored by git)
│   ├── __init__.py
│   ├── agent.py                # Defines the main GKE agent and its tools
│   ├── deployments/            # Example GKE deployment configurations
│   │   └── hello-world/        # Sample "hello-world" web application
│   │       ├── Dockerfile
│   │       ├── main.py
│   │       └── requirements.txt
│   ├── jobs/                   # Example GKE job configurations
│   │   └── hello-world/        # Sample "hello-world" batch job
│   │       ├── Dockerfile
│   │       └── main.py
│   └── tools/                  # Custom tools for the agent
│       ├── __init__.py
│       ├── docker_tools.py     # Tools for building and pushing Docker images
│       └── gke_tools.py        # Tools for interacting with GKE (jobs, deployments)
├── .gitignore
├── README.md                   # This file
├── requirements.txt            # Python dependencies
└── setup.sh                    # Script to set up the Google Cloud environment
```

## Usage

### Running the Agent

To start the agent and interact with it, you can use the Google ADK CLI. Ensure your virtual environment is activated and you are in the `gke-agent-workshop` directory.

```bash
adk web
```

This will start an interactive chat session on localhost.

### Example Interactions

Once the chat session starts, you can ask the agent to perform tasks:

*   **List GKE Jobs:**
    > "List all jobs in the GKE cluster."
    > "Show me the jobs in the default namespace."

*   **Run a GKE Job:**
    First, you might need to build and push the sample job image if you haven't already. You can ask the agent to do this or do it manually (see `kube_agent/tools/docker_tools.py` for an example).
    Assuming the image `us-central1-docker.pkg.dev/your-gcp-project-id/ai-docker-repo/hello-world-job:latest` exists:
    > "Run a job named 'my-first-job' using the image 'us-central1-docker.pkg.dev/your-gcp-project-id/ai-docker-repo/hello-world-job:latest' in the default namespace."

*   **Create a GKE Deployment:**
    Similar to jobs, ensure the image for the deployment exists in Artifact Registry. The `kube_agent/deployments/hello-world/` directory contains a sample web application.
    > "Create a deployment named 'hello-web-app' using the image 'us-central1-docker.pkg.dev/your-gcp-project-id/ai-docker-repo/hello-world-server:latest'. Expose it on port 80."

*   **Get Deployment Status:**
    > "What is the status of the 'hello-web-app' deployment?"

*   **Build and Push a Docker Image:**
    > "Build the docker image located at './kube_agent/jobs/hello-world' for platform 'linux/amd64' and push it to 'us-central1-docker.pkg.dev/your-gcp-project-id/ai-docker-repo/my-custom-job:v1'."
    *(Ensure the path and image name are correct for your project)*

## Tools

The agent is equipped with the following custom tools:

*   **`gke_tools.py`**:
    *   `get_gke_jobs_list`: Lists jobs in the GKE cluster.
    *   `run_job_in_gke`: Creates and runs a new job in GKE.
    *   `create_gke_deployment`: Creates a new deployment in GKE and optionally exposes it with a service.
    *   `get_gke_deployment_status`: Retrieves the status of a specific deployment.
    *   `get_gke_deployments_details`: Lists all deployments with their details.
*   **`docker_tools.py`**:
    *   `build_and_push_platform_image`: Builds a Docker image from a local Dockerfile context for a specified platform and pushes it to a container registry (e.g., Google Artifact Registry).

## Development

### Testing Tools Locally

The tool scripts (`docker_tools.py` and `gke_tools.py`) have `if __name__ == "__main__":` blocks that allow you to test their functionality directly.
Before running them, ensure:
1.  Your Python virtual environment is active.
2.  You have authenticated with Google Cloud (`gcloud auth application-default login`).
3.  The environment variables in `kube_agent/.env` are correctly set for your project.

For example, to test the Docker tools:
```bash
python kube_agent/tools/docker_tools.py
```
And for GKE tools:
```bash
python kube_agent/tools/gke_tools.py
```
Review the `if __name__ == "__main__":` sections in these files for specific test configurations and adjust them as needed.

# VertexAI Sprint 2025
Google Cloud credits are provided for this project #AISprint