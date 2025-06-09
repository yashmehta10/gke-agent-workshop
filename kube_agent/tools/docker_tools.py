import os
import docker # Docker SDK for Python: pip install docker
from docker.errors import BuildError, APIError as DockerAPIError, ImageNotFound
import time
import json
import platform as platform_lib # Renamed to avoid conflict with the 'platform' argument
from typing import Optional

def _get_current_platform_string() -> str:
    """
    Determines the Docker platform string for the current system's architecture,
    hardcoding the OS to 'linux' which is standard for containers.
    e.g., 'linux/amd64' or 'linux/arm64'.

    Returns:
        The Docker-compatible platform string.
    
    Raises:
        ValueError: If the system architecture is not supported or recognized.
    """
    # For containerization, the target OS is almost always 'linux'.
    # We will hardcode this and dynamically determine the architecture.
    system = "linux" 
    machine = platform_lib.machine()

    # Mapping from platform.machine() to Docker's architecture naming
    arch_mapping = {
        'x86_64': 'amd64',
        'amd64': 'amd64',
        'aarch64': 'arm64',
        'arm64': 'arm64',
    }
    
    arch = arch_mapping.get(machine.lower())
    
    if not arch:
        raise ValueError(f"Unsupported machine architecture: '{machine}'. Cannot determine Docker platform.")
        
    return f"{system}/{arch}"


def build_and_push_platform_image(
    local_context_path: str,
    full_image_name_for_registry: str, # e.g., "us-central1-docker.pkg.dev/project/repo/image:tag"
    platform: Optional[str] = None # Now optional. If None, it will be auto-detected.
) -> dict:
    """
    Builds a Docker image from a local context for a specific platform and then
    pushes it to the specified container registry. If the platform is not provided,
    it will be detected from the host system (as linux/arch).

    Args:
        local_context_path: Absolute or relative path to the directory containing 
                            the Dockerfile and build context.
        full_image_name_for_registry: The full name (including registry, project,
                                      repository, image name, and tag) to assign to 
                                      the image and push to.
        platform: The target platform for the build (e.g., "linux/amd64"). If None,
                  the platform of the host machine will be used, defaulting to a
                  Linux OS target.

    Returns:
        A dictionary containing the status of the build and push operations,
        logs, and any relevant messages or error details.
    """
    result = {
        "input_parameters": {
            "local_context_path": local_context_path,
            "full_image_name_for_registry": full_image_name_for_registry,
            "platform": platform,
        },
        "image_build_status": "pending",
        "image_build_log": [],
        "image_build_message": None,
        "image_id": None,
        "image_push_status": "pending",
        "image_push_log": [],
        "image_push_message": None,
    }

    try:
        # 0. Determine platform if not provided
        platform_was_detected = False
        if platform is None:
            try:
                # This will now correctly return 'linux/arm64' on an M1/M2 Mac.
                platform = _get_current_platform_string()
                platform_was_detected = True
            except ValueError as e:
                result["image_build_status"] = "error"
                result["image_build_message"] = f"Failed to auto-detect platform: {e}"
                return result

        result["input_parameters"]["platform"] = platform

        # 1. Validate local_context_path
        abs_local_context_path = os.path.abspath(local_context_path)
        if not os.path.isdir(abs_local_context_path):
            result["image_build_status"] = "error"
            result["image_build_message"] = f"Local context path '{abs_local_context_path}' does not exist or is not a directory."
            return result
        if not os.path.exists(os.path.join(abs_local_context_path, "Dockerfile")):
            result["image_build_status"] = "error"
            result["image_build_message"] = f"Dockerfile not found in '{abs_local_context_path}'."
            return result
        result["input_parameters"]["local_context_path"] = abs_local_context_path

        # 2. Build Docker Image for the specified platform
        docker_client = docker.from_env() 
        
        effective_nocache = bool(platform) # If platform is specified (even if auto-detected), nocache=True
        build_message_suffix = "(nocache=True, pull=True due to specified platform)" if effective_nocache else "(using Docker default cache)"
        build_attempt_message = f"Attempting to build image: {full_image_name_for_registry} for platform {platform} from path: {abs_local_context_path} {build_message_suffix}"
        if platform_was_detected:
             result["image_build_message"] = f"Platform auto-detected as {platform}. {build_attempt_message}"
        else:
             result["image_build_message"] = build_attempt_message
        
        built_image_obj = None
        try:
            built_image_obj, build_log_stream = docker_client.images.build(
                path=abs_local_context_path,
                tag=full_image_name_for_registry,
                platform=platform,
                rm=True,      
                forcerm=True,
                nocache=effective_nocache, 
                pull=effective_nocache # Also try to pull base image if building for a specific platform
            )
            for chunk in build_log_stream:
                if 'stream' in chunk:
                    log_line = chunk['stream'].strip()
                    if log_line: 
                        result["image_build_log"].append(log_line)
            
            result["image_build_status"] = "success"
            result["image_build_message"] = f"Image {full_image_name_for_registry} (platform: {platform}) built successfully."
            result["image_id"] = built_image_obj.id if hasattr(built_image_obj, 'id') else None

        except BuildError as e:
            result["image_build_status"] = "error"
            result["image_build_message"] = f"Docker build failed for platform {platform}: {str(e)}"
            for log_entry in e.build_log: 
                if 'stream' in log_entry:
                    log_line = log_entry['stream'].strip()
                    if log_line: result["image_build_log"].append(log_line)
                elif 'error' in log_entry and log_entry.get('errorDetail'):
                     result["image_build_log"].append(f"ERROR: {log_entry['errorDetail'].get('message', str(log_entry['errorDetail']))}")
                elif 'error' in log_entry: 
                     result["image_build_log"].append(f"ERROR: {str(log_entry['error'])}")
            return result 
        except DockerAPIError as e:
            result["image_build_status"] = "error"
            result["image_build_message"] = f"Docker API error during build for platform {platform}: {str(e)}"
            return result 
        except Exception as e: 
            result["image_build_status"] = "error"
            result["image_build_message"] = f"An unexpected error occurred during image build for platform {platform}: {str(e)}"
            return result


        # 3. Push the built and tagged image
        if result["image_build_status"] == "success":
            result["image_push_message"] = f"Attempting to push image: {full_image_name_for_registry} (platform: {platform})"
            try:
                push_log_stream = docker_client.images.push(full_image_name_for_registry, stream=True, decode=True)
                
                push_had_error_in_stream = False
                for chunk in push_log_stream:
                    status_msg = chunk.get('status', '')
                    progress_msg = chunk.get('progress', '')
                    error_msg_detail = chunk.get('errorDetail', {}).get('message', '') 
                    error_msg_top = chunk.get('error', '') 

                    log_entry = f"{status_msg} {progress_msg} {error_msg_detail}".strip()
                    if log_entry: result["image_push_log"].append(log_entry)
                    
                    if error_msg_detail or error_msg_top: 
                        final_error_msg = error_msg_detail if error_msg_detail else error_msg_top
                        push_had_error_in_stream = True 
                        result["image_push_message"] = f"Push failed with error in stream: {final_error_msg}"

                if push_had_error_in_stream:
                    result["image_push_status"] = "error"
                else: 
                    result["image_push_status"] = "success"
                    result["image_push_message"] = f"Image {full_image_name_for_registry} pushed successfully."

            except DockerAPIError as e:
                result["image_push_status"] = "error"
                result["image_push_message"] = f"Docker API error during push for {full_image_name_for_registry}: {str(e)}"
            except Exception as e:
                result["image_push_status"] = "error"
                result["image_push_message"] = f"An unexpected error occurred during image push: {str(e)}"
        else:
            result["image_push_status"] = "skipped"
            result["image_push_message"] = "Push skipped due to build failure."
        
    except Exception as e: 
        if result["image_build_status"] == "pending" and result["image_push_status"] == "pending":
            result["image_build_status"] = "error" 
            result["image_build_message"] = f"An unexpected top-level error occurred: {str(e)}"
            result["image_push_status"] = "skipped"
        
    return result


if __name__ == "__main__":
    # --- Configuration for Local Testing ---
    # IMPORTANT: Update this to a valid path on your local machine that contains a Dockerfile.
    TEST_LOCAL_CONTEXT = os.getenv("DOCKER_TOOL_LOCAL_CONTEXT", "/Users/yashmehta/Desktop/personal/github/gke-agent-workshop/kube_agent/jobs/hello-world")
    
    # --- Create a dummy Dockerfile for testing if one doesn't exist ---
    if not os.path.exists(TEST_LOCAL_CONTEXT):
        print(f"Creating dummy test directory and Dockerfile at: '{TEST_LOCAL_CONTEXT}'")
        os.makedirs(TEST_LOCAL_CONTEXT, exist_ok=True)
    dockerfile_path = os.path.join(TEST_LOCAL_CONTEXT, "Dockerfile")
    if not os.path.exists(dockerfile_path):
        with open(dockerfile_path, "w") as f:
            f.write("FROM alpine:latest\n")
            f.write('CMD ["echo", "Hello from the dynamically built container!"]\n')

    # --- Image and Registry Configuration ---
    # IMPORTANT: Update these values with your Google Cloud Project and Artifact Registry details.
    TEST_AR_HOST = os.getenv("TEST_AR_HOST", "us-central1-docker.pkg.dev") 
    TEST_GCP_PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "yash-sandbox-424323") 
    TEST_AR_REPOSITORY_NAME = os.getenv("TEST_AR_REPOSITORY_NAME", "ai-docker-repo") 
    TEST_IMAGE_NAME_IN_REPO = os.getenv("TEST_AR_IMAGE_NAME", "hello-world-dynamic") 
    TEST_IMAGE_TAG = os.getenv("TEST_AR_IMAGE_TAG", f"latest")
    
    # --- Platform Configuration: Set to None for auto-detection ---
    # To override, set this to a specific string like "linux/amd64" or "linux/arm64".
    TEST_PLATFORM = None 

    FULL_IMAGE_NAME_FOR_REGISTRY = f"{TEST_AR_HOST}/{TEST_GCP_PROJECT_ID}/{TEST_AR_REPOSITORY_NAME}/{TEST_IMAGE_NAME_IN_REPO}:{TEST_IMAGE_TAG}"

    print(f"--- Test Configuration for Platform-Specific Docker Build and Push ---")
    print(f"Local Context Path: {os.path.abspath(TEST_LOCAL_CONTEXT)}")
    print(f"Full Image Name for Registry: {FULL_IMAGE_NAME_FOR_REGISTRY}")
    if TEST_PLATFORM is None:
        try:
            detected_platform = _get_current_platform_string()
            print(f"Target Platform: Dynamically Detected as '{detected_platform}'")
        except ValueError as e:
            print(f"Target Platform: Could not be detected. Error: {e}")
    else:
        print(f"Target Platform: Manually set to '{TEST_PLATFORM}'")
    print(f"--- Starting Docker Build and Push ---")

    abs_test_context_path = os.path.abspath(TEST_LOCAL_CONTEXT)
    if not os.path.isdir(abs_test_context_path) or \
       not os.path.exists(os.path.join(abs_test_context_path, "Dockerfile")):
        print(f"\nERROR: TEST_LOCAL_CONTEXT ('{abs_test_context_path}') is not a valid directory with a Dockerfile.")
    elif "your-gcp-project-id" in TEST_GCP_PROJECT_ID: 
        print("\nERROR: Please update placeholder GCP project ID (TEST_GCP_PROJECT_ID) or set the GOOGLE_CLOUD_PROJECT environment variable.")
    else:
        result = build_and_push_platform_image(
            local_context_path=TEST_LOCAL_CONTEXT,
            full_image_name_for_registry=FULL_IMAGE_NAME_FOR_REGISTRY,
            platform=TEST_PLATFORM # Pass None to trigger auto-detection
        )
        print("\n--- Docker Build and Push Operation Result (JSON) ---")
        print(json.dumps(result, indent=2))

        print("\n--- Summary ---")
        print(f"Full image name: {result.get('input_parameters', {}).get('full_image_name_for_registry')}")
        print(f"Target Platform: {result.get('input_parameters', {}).get('platform')}")
        print(f"Build Status: {result.get('image_build_status')} - {result.get('image_build_message')}")
        print(f"Push Status: {result.get('image_push_status')} - {result.get('image_push_message')}")
        
        if result.get('image_build_log'):
            print("\n--- Build Log Snippet (last 10 lines) ---")
            for log_line in result.get('image_build_log', [])[-10:]: print(log_line)

        if result.get('image_push_log'):
            print("\n--- Push Log Snippet (last 10 lines) ---")
            for log_line in result.get('image_push_log', [])[-10:]: print(log_line)
        
        if result.get("image_push_status") == "success":
            ar_region = TEST_AR_HOST.split('-docker.pkg.dev')[0]
            ar_image_path_for_console = TEST_IMAGE_NAME_IN_REPO.replace('/', '%2F')
            print(f"\nImage pushed successfully. You can find it at: https://console.cloud.google.com/artifacts/docker/{TEST_GCP_PROJECT_ID}/{ar_region}/repositories/{TEST_AR_REPOSITORY_NAME}/images/{ar_image_path_for_console} (tag: {TEST_IMAGE_TAG})")
