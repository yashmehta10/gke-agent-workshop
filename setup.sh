#!/bin/bash

# ==============================================================================
#  GKE Workshop Setup Script
#  This script prepares a new Google Cloud project for the workshop by:
#  1. Enabling required APIs.
#  2. Creating a GKE Autopilot cluster.
#  3. Creating an Artifact Registry Docker repository.
#  4. Configuring local Docker client authentication.
# ==============================================================================

# --- Configuration ---
# You can change these variables if you want different names for your resources.
GKE_CLUSTER_NAME="autopilot-cluster-1"
GKE_REGION="us-central1"
AR_REPO_NAME="ai-docker-repo"
AR_LOCATION="us-central1" # Must be a region, not a multi-region, for Docker repos

# --- Helper Functions for Colors and Output ---
print_header() {
  echo ""
  echo "==> $(tput bold)$1$(tput sgr0)"
  echo "--------------------------------------------------------"
}

print_success() {
  echo "$(tput setaf 2)✔ $1$(tput sgr0)"
}

print_error() {
  echo "$(tput setaf 1)✘ ERROR: $1$(tput sgr0)"
}

# --- Check for gcloud CLI ---
if ! command -v gcloud &> /dev/null
then
    print_error "'gcloud' command-line tool not found. Please install the Google Cloud SDK and ensure it's in your PATH."
    exit 1
fi

# --- 1. Set Project ID ---
print_header "Setting up Google Cloud Project"

# Get the current project ID, or prompt the user if not set
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT_ID" ]; then
    read -p "Please enter your Google Cloud Project ID: " PROJECT_ID
    if [ -z "$PROJECT_ID" ]; then
        print_error "Project ID cannot be empty."
        exit 1
    fi
fi

# Set the project for the current gcloud configuration
gcloud config set project "$PROJECT_ID"
echo "Working with project: $(tput bold)$PROJECT_ID$(tput sgr0)"


# --- 2. Enable Required Services (APIs) ---
print_header "Enabling required Google Cloud services..."

# List of services to enable
SERVICES=(
  "container.googleapis.com"        # Google Kubernetes Engine API
  "artifactregistry.googleapis.com" # Artifact Registry API
  "cloudbuild.googleapis.com"       # Cloud Build API (useful for future automation)
  "aiplatform.googleapis.com"       # Vertex AI API (for AI/ML models and agents)
)

for SERVICE in "${SERVICES[@]}"; do
  # Check if the service is already enabled
  if gcloud services list --enabled --filter="name:$SERVICE" --format="value(name)" | grep -q "$SERVICE"; then
    print_success "$SERVICE is already enabled."
  else
    echo "Enabling $SERVICE..."
    gcloud services enable "$SERVICE" --project="$PROJECT_ID"
    if [ $? -eq 0 ]; then
        print_success "Successfully enabled $SERVICE."
    else
        print_error "Failed to enable $SERVICE. Please check permissions."
        exit 1
    fi
  fi
done


# --- 3. Create GKE Autopilot Cluster ---
print_header "Creating GKE Autopilot Cluster"
echo "This step can take several minutes..."

# Check if the cluster already exists
if gcloud container clusters describe "$GKE_CLUSTER_NAME" --region="$GKE_REGION" --project="$PROJECT_ID" &>/dev/null; then
  print_success "GKE cluster '$GKE_CLUSTER_NAME' already exists in region '$GKE_REGION'."
else
  gcloud beta container --project "$PROJECT_ID" clusters create-auto "$GKE_CLUSTER_NAME" \
      --region "$GKE_REGION"

  if [ $? -eq 0 ]; then
      print_success "Successfully created GKE cluster '$GKE_CLUSTER_NAME'."
  else
      print_error "Failed to create GKE cluster. Please check the logs above for details."
      exit 1
  fi
fi


# --- 4. Create Artifact Registry Repository ---
print_header "Creating Artifact Registry Docker Repository"

# Check if the repository already exists
if gcloud artifacts repositories describe "$AR_REPO_NAME" --location="$AR_LOCATION" --project="$PROJECT_ID" &>/dev/null; then
  print_success "Artifact Registry repository '$AR_REPO_NAME' already exists in location '$AR_LOCATION'."
else
  gcloud artifacts repositories create "$AR_REPO_NAME" \
      --repository-format=docker \
      --location="$AR_LOCATION" \
      --description="Docker repository for AI agent workshop" \
      --project="$PROJECT_ID"

  if [ $? -eq 0 ]; then
      print_success "Successfully created Artifact Registry repository '$AR_REPO_NAME'."
  else
      print_error "Failed to create Artifact Registry repository. Please check permissions."
      exit 1
  fi
fi


# --- 5. Configure Docker Authentication ---
print_header "Configuring Docker Authentication for Artifact Registry"
gcloud auth configure-docker "${AR_LOCATION}-docker.pkg.dev" --project="$PROJECT_ID"

if [ $? -eq 0 ]; then
    print_success "Docker authentication configured for ${AR_LOCATION}-docker.pkg.dev"
else
    print_error "Failed to configure Docker authentication."
    exit 1
fi


# --- Final Summary ---
print_header "🎉 Workshop Setup Complete! 🎉"
echo "Your Google Cloud project '$PROJECT_ID' is now configured."
echo ""
echo "Summary of resources:"
echo "  - GKE Cluster:      $(tput bold)$GKE_CLUSTER_NAME$(tput sgr0) in region $(tput bold)$GKE_REGION$(tput sgr0)"
echo "  - Artifact Registry:  $(tput bold)$AR_REPO_NAME$(tput sgr0) in location $(tput bold)$AR_LOCATION$(tput sgr0)"
echo ""
echo "Next Steps:"
echo "1. Verify you can connect to your cluster with: $(tput setaf 6)gcloud container clusters get-credentials $GKE_CLUSTER_NAME --region $GKE_REGION$(tput sgr0)"
echo "2. Check your connection with: $(tput setaf 6)kubectl get nodes$(tput sgr0)"
echo ""
