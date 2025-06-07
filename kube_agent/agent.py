from google.adk.agents import Agent
from .tools.gke_tools import run_job_in_gke, get_gke_jobs_list, create_gke_deployment, get_gke_deployment_status, get_gke_deployments_details
from .tools.docker_tools import build_and_push_platform_image

root_agent = Agent(
    name="kube_agent",
    model="gemini-2.0-flash",
    description=(
        "Agent to manage GKE cluster"
    ),
    instruction=(
        "You are an expert GKE assistant. When a user asks you to list jobs, "
        "do not output the raw JSON data you receive from your tools. "
        "Instead, summarize the list of jobs in a clear, human-readable format. "
        "For each job, state its name, its final status (Succeeded or Failed), "
        "and its completion time. Start with the most recent jobs first."
    ),
    tools=[get_gke_jobs_list, run_job_in_gke, create_gke_deployment, get_gke_deployment_status, get_gke_deployments_details, build_and_push_platform_image],
)