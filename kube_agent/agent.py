import os
from google.adk.agents import Agent
from .tools.gke_tools import run_job_in_gke, get_gke_jobs_list, create_gke_deployment, get_gke_deployment_status, get_gke_deployments_details
from .tools.docker_tools import build_and_push_platform_image
# from .tools.dummy_tools import today_date

project=os.getenv("GOOGLE_CLOUD_PROJECT", "yash-sandbox-424323")
cluster_location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
cluster_name=os.getenv("GKE_CLUSTER_NAME", "autopilot-cluster-1")
artifact_repo_name=os.getenv("TEST_AR_REPOSITORY_NAME", "ai-docker-repo")

root_agent = Agent(
    name="kube_agent",
    model="gemini-2.0-flash",
    description=(
        "Agent to manage GKE cluster"
    ),
    instruction=(
        f"""You are an expert GKE assistant. When a user asks you to list jobs,
        do not output the raw JSON data you receive from your tools.
        Instead, summarize the list of jobs in a clear, human-readable format.
        For each job, state its name, its final status (Succeeded or Failed),
        and its completion time. Start with the most recent jobs first
        Here are all the important variables you might need:
        GKE Project ID: {project}"
        GKE Cluster Location: {cluster_location}
        GKE Cluster Name: {cluster_name}
        Artifact Repository Name: {artifact_repo_name}
        """
    ),
    tools=[get_gke_jobs_list, run_job_in_gke, create_gke_deployment, get_gke_deployment_status, get_gke_deployments_details, build_and_push_platform_image],
    # tools=[today_date]
)