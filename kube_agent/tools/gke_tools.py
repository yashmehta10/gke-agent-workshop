import os
from kubernetes import client
from kubernetes.client.exceptions import ApiException as K8sApiException
from google.cloud import container_v1
from google.auth import default as google_auth_default
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.api_core import exceptions as google_exceptions
import tempfile
import base64
import json
import time
from typing import Optional, List, Dict 

# --- Configuration for API Timeouts ---
GCP_API_TIMEOUT = 45 
K8S_API_TIMEOUT_CONNECTIVITY = 15 
K8S_API_TIMEOUT_JOB_CREATE = 60 
K8S_API_TIMEOUT_JOB_LIST = 60
K8S_API_TIMEOUT_DEPLOYMENT_LIST = 60
K8S_API_TIMEOUT_DEPLOYMENT_CREATE = 60
K8S_API_TIMEOUT_GENERAL = 30 
K8S_JOB_WAIT_TIMEOUT_SECONDS = 300 
K8S_SERVICE_WAIT_TIMEOUT_SECONDS = 180 # Time to wait for LoadBalancer IP
K8S_JOB_WAIT_POLL_INTERVAL_SECONDS = 10 
K8S_API_TIMEOUT_STATUS_READ = 20 

# --- GKE Helper Function: Get Cluster Connection Info ---
def _get_gke_cluster_connection_info(project_id: str, location: str, cluster_name: str) -> dict:
    """Retrieves GKE cluster endpoint and CA certificate data."""
    try:
        container_client = container_v1.ClusterManagerClient()
        cluster_path = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"
        gke_cluster = container_client.get_cluster(name=cluster_path, timeout=GCP_API_TIMEOUT)
        if not gke_cluster:
            raise google_exceptions.NotFound(f"Cluster {cluster_path} not found after API call.")
        return {
            "status": "success",
            "connection_info": {"endpoint": gke_cluster.endpoint, "ca_data": gke_cluster.master_auth.cluster_ca_certificate},
            "message": "Cluster connection information retrieved successfully."
        }
    except google_exceptions.DeadlineExceeded as e:
        return {"status": "error", "connection_info": None, "message": f"Timeout (after {GCP_API_TIMEOUT}s) fetching GKE cluster details: {str(e)}"}
    except google_exceptions.NotFound as e:
        return {"status": "error", "connection_info": None, "message": f"GKE cluster not found: {str(e)}"}
    except google_exceptions.GoogleAPICallError as e: 
        return {"status": "error", "connection_info": None, "message": f"Google API error fetching cluster details: {str(e)}"}
    except Exception as e:
        return {"status": "error", "connection_info": None, "message": f"Failed to retrieve cluster connection info: {str(e)}"}

# --- GKE Helper Function: Configure Kubernetes API Client ---
def _configure_kubernetes_api_client(cluster_conn_info: dict) -> dict:
    """Configures the Kubernetes API client using direct token authentication."""
    temp_ca_cert_path = None
    try:
        effective_config = client.Configuration()
        effective_config.host = f"https://{cluster_conn_info['endpoint']}"
        if cluster_conn_info.get('ca_data'):
            fd, temp_ca_cert_path = tempfile.mkstemp(suffix=".crt", prefix="gke_ca_")
            with os.fdopen(fd, 'wb') as tmp_file:
                tmp_file.write(base64.b64decode(cluster_conn_info['ca_data']))
            effective_config.ssl_ca_cert = temp_ca_cert_path
        else:
            effective_config.verify_ssl = False 
        credentials, _ = google_auth_default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        auth_gcp_request = GoogleAuthRequest()
        credentials.refresh(auth_gcp_request) 
        if not credentials.token: raise Exception("Failed to obtain GCP access token.")
        effective_config.api_key_prefix['authorization'] = 'Bearer'
        effective_config.api_key['authorization'] = credentials.token
        client.Configuration.set_default(effective_config) 
        return {"status": "success", "message": "Kubernetes client configured.", "temp_ca_path": temp_ca_cert_path}
    except Exception as e:
        if temp_ca_cert_path and os.path.exists(temp_ca_cert_path): 
            try: os.remove(temp_ca_cert_path)
            except: pass 
        return {"status": "error", "message": f"Failed to configure K8s client: {str(e)}", "temp_ca_path": None}

# --- GKE Helper Function: Get Job Pod Logs ---
def _get_job_pod_logs(core_v1_api: client.CoreV1Api, namespace: str, job_name: str) -> list:
    """Fetches logs from the pod(s) created by a given Job."""
    pod_logs = []
    try:
        pods = core_v1_api.list_namespaced_pod(
            namespace=namespace,
            label_selector=f"job-name={job_name}",
            timeout_seconds=K8S_API_TIMEOUT_STATUS_READ 
        )
        if not pods.items:
            pod_logs.append(f"Info: No pods found for job-name: {job_name}.")
            return pod_logs
        pods.items.sort(key=lambda p: p.metadata.creation_timestamp, reverse=True)
        selected_pod = pods.items[0]
        pod_name = selected_pod.metadata.name
        if selected_pod.status.phase == "Pending":
             pod_logs.append(f"Info: Pod {pod_name} is in Pending phase. Check pod events for details (e.g., ImagePullBackOff).")
        elif selected_pod.status.phase in ["Succeeded", "Failed", "Running"]:
            log_stream = core_v1_api.read_namespaced_pod_log(
                name=pod_name, namespace=namespace, timestamps=True, _request_timeout=K8S_API_TIMEOUT_STATUS_READ
            )
            pod_logs.extend(log_stream.splitlines())
        else:
            pod_logs.append(f"Info: Pod {pod_name} is in phase {selected_pod.status.phase}.")
    except K8sApiException as e:
        pod_logs.append(f"Warning: K8s API error fetching pod logs: {e.status} - {e.reason}. Body: {e.body}")
    except Exception as e: 
        pod_logs.append(f"Warning: Unexpected error fetching pod logs: {str(e)}")
    return pod_logs if pod_logs else ["Info: No logs were retrieved."]

# --- GKE Job Function ---
def run_job_in_gke(
    ar_image_name_with_tag: str, 
    gke_project_id: str,
    gke_location: str,
    gke_cluster_name: str,
    job_name: str,
    namespace: str = "default",
    container_name: Optional[str] = None, 
    command: Optional[List[str]] = None, 
    args: Optional[List[str]] = None, 
    env_vars: Optional[Dict[str, str]] = None, 
    restart_policy: str = "OnFailure", 
    completions: int = 1,
    parallelism: int = 1,
    backoff_limit: int = 4, 
    active_deadline_seconds: Optional[int] = None 
) -> dict:
    """
    Runs a container image as a Job in a specified GKE cluster, waits for its
    completion (or failure/timeout), and attempts to fetch execution logs.
    """
    result = {
        "input_parameters": locals(),
        "cluster_data_retrieval_status": "pending", "cluster_data_retrieval_message": None,
        "client_config_status": "pending", "client_config_message": None,
        "job_create_status": "pending", "job_create_message": None, "job_details": None,
        "job_final_status": "Unknown", "job_final_message": "Job outcome not yet determined.",
        "pod_logs": []
    }
    temp_ca_path_for_cleanup = None
    k8s_job_name = job_name.lower().replace("_", "-") 
    k8s_container_name = (container_name or k8s_job_name).lower().replace("_", "-")

    try:
        cluster_data_res = _get_gke_cluster_connection_info(gke_project_id, gke_location, gke_cluster_name)
        result.update(cluster_data_retrieval_status=cluster_data_res["status"], cluster_data_retrieval_message=cluster_data_res["message"])
        if cluster_data_res["status"] == "error": return result
        
        client_config_res = _configure_kubernetes_api_client(cluster_data_res["connection_info"])
        result.update(client_config_status=client_config_res["status"], client_config_message=client_config_res["message"])
        temp_ca_path_for_cleanup = client_config_res.get("temp_ca_path")
        if client_config_res["status"] == "error": return result
        
        batch_v1_api = client.BatchV1Api()
        core_v1_api = client.CoreV1Api() 

        env_list = [client.V1EnvVar(name=name, value=value) for name, value in env_vars.items()] if env_vars else None
        container = client.V1Container(name=k8s_container_name, image=ar_image_name_with_tag, command=command, args=args, env=env_list)
        pod_spec = client.V1PodSpec(restart_policy=restart_policy, containers=[container])
        pod_template_spec = client.V1PodTemplateSpec(metadata=client.V1ObjectMeta(labels={"app": k8s_job_name, "job-name": k8s_job_name}), spec=pod_spec)
        job_spec = client.V1JobSpec(template=pod_template_spec, backoff_limit=backoff_limit, completions=completions, parallelism=parallelism, active_deadline_seconds=active_deadline_seconds)
        job_body = client.V1Job(api_version="batch/v1", kind="Job", metadata=client.V1ObjectMeta(name=k8s_job_name, namespace=namespace), spec=job_spec)

        result["job_create_message"] = f"Attempting to create Job '{k8s_job_name}' in namespace '{namespace}'."
        try:
            api_response = batch_v1_api.create_namespaced_job(body=job_body, namespace=namespace, _request_timeout=K8S_API_TIMEOUT_JOB_CREATE)
            result.update(job_create_status="success", job_create_message=f"Job '{k8s_job_name}' created successfully.",
                          job_details={"name": api_response.metadata.name, "namespace": api_response.metadata.namespace, "uid": api_response.metadata.uid, "creation_timestamp": str(api_response.metadata.creation_timestamp)})
        except K8sApiException as e:
            result.update(job_create_status="error", job_create_message=f"K8s API error creating Job: {e.status} - {e.reason}. Details: {e.body}"); return result 

        if result["job_create_status"] == "success":
            start_time = time.time()
            job_succeeded = False
            job_failed = False 
            while time.time() - start_time < K8S_JOB_WAIT_TIMEOUT_SECONDS:
                try:
                    job_status_response = batch_v1_api.read_namespaced_job_status(name=k8s_job_name, namespace=namespace, _request_timeout=K8S_API_TIMEOUT_STATUS_READ)
                    status = job_status_response.status
                    succeeded_pods = status.succeeded if status.succeeded is not None else 0
                    failed_pods_count = status.failed if status.failed is not None else 0 
                    if succeeded_pods >= completions:
                        result.update(job_final_status="Succeeded", job_final_message=f"Job '{k8s_job_name}' completed successfully."); job_succeeded = True; break
                    if status.conditions:
                        for condition in status.conditions:
                            if condition.type == "Failed" and condition.status == "True":
                                result.update(job_final_status="Failed", job_final_message=f"Job '{k8s_job_name}' marked as failed. Reason: {condition.reason}, Message: {condition.message}"); job_failed = True; break 
                        if job_failed: break 
                    if backoff_limit == 0 and failed_pods_count > 0 and not job_failed: 
                        result.update(job_final_status="Failed", job_final_message=f"Job '{k8s_job_name}' failed (backoffLimit is 0 and {failed_pods_count} pod(s) failed)."); job_failed = True; break
                except K8sApiException as e:
                    result.update(job_final_status="ErrorCheckingStatus", job_final_message=f"API error while checking job status: {str(e)}"); job_failed = True; break 
                if job_succeeded or job_failed: break
                time.sleep(K8S_JOB_WAIT_POLL_INTERVAL_SECONDS)
            if not (job_succeeded or job_failed): 
                result.update(job_final_status="TimeoutWaitingForCompletion", job_final_message=f"Timed out after {K8S_JOB_WAIT_TIMEOUT_SECONDS}s waiting for job '{k8s_job_name}' to complete.")
            result["pod_logs"] = _get_job_pod_logs(core_v1_api, namespace, k8s_job_name)
    except Exception as e:
        if result["job_create_status"] == "pending": result.update(job_create_status="error", job_create_message=f"Unexpected error during Job phase: {str(e)}")
        elif result["client_config_status"] == "pending": result.update(client_config_status="error", client_config_message=f"Unexpected error during K8s client setup: {str(e)}")
        else:
            if result.get("cluster_data_retrieval_status") != "error": result.update(cluster_data_retrieval_status="error", cluster_data_retrieval_message=f"Top-level error: {str(e)}")
    finally:
        if temp_ca_path_for_cleanup and os.path.exists(temp_ca_path_for_cleanup):
            try: os.remove(temp_ca_path_for_cleanup)
            except Exception: pass 
    return result

# --- GKE Create Deployment Function ---
def create_gke_deployment(
    ar_image_name_with_tag: str, 
    gke_project_id: str,
    gke_location: str,
    gke_cluster_name: str,
    deployment_name: str,
    namespace: str = "default",
    container_name: Optional[str] = None,
    replicas: int = 1,
    container_port: int = 8080, 
    env_vars: Optional[Dict[str, str]] = None, 
    service_type: Optional[str] = "LoadBalancer", 
    service_port: int = 80 
) -> dict:
    """
    Creates a Kubernetes Deployment and optionally a Service to expose it.
    If a LoadBalancer service is created, it will wait for the external IP to be assigned.
    """
    result = {
        "input_parameters": locals(), 
        "cluster_data_retrieval_status": "pending", "cluster_data_retrieval_message": None,
        "client_config_status": "pending", "client_config_message": None,
        "deployment_create_status": "pending", "deployment_create_message": None, "deployment_details": None,
        "service_create_status": "pending", "service_create_message": None, "service_details": None
    }
    temp_ca_path_for_cleanup = None
    k8s_deployment_name = deployment_name.lower().replace("_", "-")
    k8s_container_name = (container_name or k8s_deployment_name).lower().replace("_", "-")
    k8s_service_name = f"{k8s_deployment_name}-svc"

    try:
        cluster_data_res = _get_gke_cluster_connection_info(gke_project_id, gke_location, gke_cluster_name)
        result.update(cluster_data_retrieval_status=cluster_data_res["status"], cluster_data_retrieval_message=cluster_data_res["message"])
        if cluster_data_res["status"] == "error": return result
        
        client_config_res = _configure_kubernetes_api_client(cluster_data_res["connection_info"])
        result.update(client_config_status=client_config_res["status"], client_config_message=client_config_res["message"])
        temp_ca_path_for_cleanup = client_config_res.get("temp_ca_path")
        if client_config_res["status"] == "error": return result
        
        apps_v1_api = client.AppsV1Api()
        core_v1_api = client.CoreV1Api()

        env_list = [client.V1EnvVar(name=name, value=value) for name, value in env_vars.items()] if env_vars else None
        container = client.V1Container(
            name=k8s_container_name, image=ar_image_name_with_tag,
            ports=[client.V1ContainerPort(container_port=container_port)], env=env_list
        )
        k8s_labels = {"app": k8s_deployment_name}
        template = client.V1PodTemplateSpec(metadata=client.V1ObjectMeta(labels=k8s_labels), spec=client.V1PodSpec(containers=[container]))
        selector = client.V1LabelSelector(match_labels=k8s_labels)
        deployment_spec = client.V1DeploymentSpec(replicas=replicas, template=template, selector=selector)
        deployment_body = client.V1Deployment(
            api_version="apps/v1", kind="Deployment",
            metadata=client.V1ObjectMeta(name=k8s_deployment_name, namespace=namespace),
            spec=deployment_spec
        )

        result["deployment_create_message"] = f"Attempting to create Deployment '{k8s_deployment_name}'..."
        try:
            api_response = apps_v1_api.create_namespaced_deployment(body=deployment_body, namespace=namespace, _request_timeout=K8S_API_TIMEOUT_DEPLOYMENT_CREATE)
            result.update(deployment_create_status="success", deployment_create_message=f"Deployment '{k8s_deployment_name}' created successfully.",
                          deployment_details={"name": api_response.metadata.name, "namespace": api_response.metadata.namespace, "uid": api_response.metadata.uid})
        except K8sApiException as e:
            result.update(deployment_create_status="error", deployment_create_message=f"K8s API error creating Deployment: {e.status} - {e.reason}. Details: {e.body}"); return result

        if service_type and result["deployment_create_status"] == "success":
            service_spec = client.V1ServiceSpec(selector=k8s_labels, ports=[client.V1ServicePort(protocol="TCP", port=service_port, target_port=container_port)], type=service_type)
            service_body = client.V1Service(api_version="v1", kind="Service", metadata=client.V1ObjectMeta(name=k8s_service_name, namespace=namespace), spec=service_spec)
            result["service_create_message"] = f"Attempting to create Service '{k8s_service_name}' of type '{service_type}'."
            try:
                service_api_response = core_v1_api.create_namespaced_service(body=service_body, namespace=namespace, _request_timeout=K8S_API_TIMEOUT_GENERAL)
                result.update(service_create_status="success", service_create_message=f"Service '{k8s_service_name}' creation initiated.")
                
                service_details = {"name": service_api_response.metadata.name, "namespace": service_api_response.metadata.namespace, "cluster_ip": service_api_response.spec.cluster_ip}
                
                if service_type == "LoadBalancer":
                    result["service_create_message"] += " Waiting for external IP..."
                    start_time = time.time()
                    while time.time() - start_time < K8S_SERVICE_WAIT_TIMEOUT_SECONDS:
                        service_status = core_v1_api.read_namespaced_service_status(name=k8s_service_name, namespace=namespace, _request_timeout=K8S_API_TIMEOUT_STATUS_READ)
                        if service_status.status.load_balancer and service_status.status.load_balancer.ingress:
                            ingress_ips = [ing.ip for ing in service_status.status.load_balancer.ingress]
                            service_details["load_balancer_ingress"] = ingress_ips
                            result.update(service_create_message=f"Service '{k8s_service_name}' is available at external IP(s): {', '.join(ingress_ips)}")
                            break
                        time.sleep(K8S_JOB_WAIT_POLL_INTERVAL_SECONDS)
                    else: # Loop timed out
                        result["service_create_message"] += f" Timed out after {K8S_SERVICE_WAIT_TIMEOUT_SECONDS}s waiting for external IP."

                result.update(service_details=service_details)
            except K8sApiException as e:
                result.update(service_create_status="error", service_create_message=f"K8s API error creating Service: {e.status} - {e.reason}. Details: {e.body}")
        else:
            result.update(service_create_status="skipped", service_create_message="Service creation not requested or skipped due to Deployment failure.")
    except Exception as e:
        if result["deployment_create_status"] == "pending": result.update(deployment_create_status="error", deployment_create_message=f"Unexpected error: {str(e)}")
        elif result["client_config_status"] == "pending": result.update(client_config_status="error", client_config_message=f"Unexpected error: {str(e)}")
        else: result.update(cluster_data_retrieval_status="error", cluster_data_retrieval_message=f"Top-level error: {str(e)}")
    finally:
        if temp_ca_path_for_cleanup and os.path.exists(temp_ca_path_for_cleanup):
            try: os.remove(temp_ca_path_for_cleanup)
            except Exception: pass 
    return result

# --- GKE Get Deployment Status Function ---
def get_gke_deployment_status(gke_project_id: str, gke_location: str, gke_cluster_name: str, deployment_name: str, namespace: str = "default") -> dict:
    """Retrieves the status of a specific Kubernetes Deployment."""
    result = {"input_parameters": locals(), "deployment_status": None, "error_message": None}
    temp_ca_path_for_cleanup = None
    try:
        cluster_data_res = _get_gke_cluster_connection_info(gke_project_id, gke_location, gke_cluster_name)
        if cluster_data_res["status"] == "error":
            result["error_message"] = cluster_data_res["message"]; return result
        
        client_config_res = _configure_kubernetes_api_client(cluster_data_res["connection_info"])
        if client_config_res["status"] == "error":
            result["error_message"] = client_config_res["message"]; return result
        temp_ca_path_for_cleanup = client_config_res.get("temp_ca_path")

        apps_v1_api = client.AppsV1Api()
        dep = apps_v1_api.read_namespaced_deployment_status(name=deployment_name, namespace=namespace, _request_timeout=K8S_API_TIMEOUT_STATUS_READ)
        status = dep.status; spec = dep.spec
        result["deployment_status"] = {
            "name": dep.metadata.name, "namespace": dep.metadata.namespace,
            "replicas_desired": spec.replicas if spec else None,
            "replicas_current": status.replicas if status and status.replicas is not None else 0,
            "replicas_ready": status.ready_replicas if status and status.ready_replicas is not None else 0,
            "replicas_available": status.available_replicas if status and status.available_replicas is not None else 0,
            "replicas_updated": status.updated_replicas if status and status.updated_replicas is not None else 0,
            "conditions": [{"type": c.type, "status": str(c.status), "reason": c.reason, "message": c.message} for c in status.conditions] if status and status.conditions else []
        }
    except K8sApiException as e:
        result["error_message"] = f"K8s API error getting deployment status: {e.status} - {e.reason}. Details: {e.body}"
    except Exception as e:
        result["error_message"] = f"An unexpected error occurred: {str(e)}"
    finally:
        if temp_ca_path_for_cleanup and os.path.exists(temp_ca_path_for_cleanup):
            try: os.remove(temp_ca_path_for_cleanup)
            except Exception: pass
    return result

# --- GKE List Deployments Function ---
def get_gke_deployments_details(gke_project_id: str, gke_location: str, gke_cluster_name: str, namespace: Optional[str] = None) -> dict:
    """Retrieves details of all Deployments in a specified GKE cluster and namespace."""
    result = {"input_parameters": locals(), "deployments_fetch_status": "pending", "deployments_fetch_message": None, "deployments": []}
    temp_ca_path_for_cleanup = None
    try:
        cluster_data_res = _get_gke_cluster_connection_info(gke_project_id, gke_location, gke_cluster_name)
        if cluster_data_res["status"] == "error":
            result.update(deployments_fetch_status="error", deployments_fetch_message=cluster_data_res["message"]); return result
        client_config_res = _configure_kubernetes_api_client(cluster_data_res["connection_info"])
        if client_config_res["status"] == "error":
            result.update(deployments_fetch_status="error", deployments_fetch_message=client_config_res["message"]); return result
        temp_ca_path_for_cleanup = client_config_res.get("temp_ca_path")
        
        apps_v1_api = client.AppsV1Api()
        if namespace:
            deployment_list = apps_v1_api.list_namespaced_deployment(namespace, timeout_seconds=K8S_API_TIMEOUT_DEPLOYMENT_LIST)
        else:
            deployment_list = apps_v1_api.list_deployment_for_all_namespaces(timeout_seconds=K8S_API_TIMEOUT_DEPLOYMENT_LIST)
        
        deployments_data = []
        for dep in deployment_list.items:
            status = dep.status; spec = dep.spec
            desired = spec.replicas if spec and spec.replicas is not None else 0
            available = status.available_replicas if status and status.available_replicas is not None else 0
            health_status = "Unknown"
            if desired == available and desired > 0:
                health_status = "Healthy"
            elif desired == 0 and available == 0:
                 health_status = "ScaledDown"
            else:
                health_status = "Progressing"
            if status and status.conditions:
                for c in status.conditions:
                    if c.type == "Progressing" and c.reason == "ProgressDeadlineExceeded":
                        health_status = "Unhealthy"; break
            
            deployments_data.append({
                "name": dep.metadata.name, "namespace": dep.metadata.namespace,
                "health_status": health_status,
                "replicas_desired": desired,
                "replicas_current": status.replicas if status and status.replicas is not None else 0,
                "replicas_ready": status.ready_replicas if status and status.ready_replicas is not None else 0,
                "replicas_available": available,
                "replicas_updated": status.updated_replicas if status and status.updated_replicas is not None else 0,
                "conditions": [{"type": c.type, "status": str(c.status), "reason": c.reason, "message": c.message} for c in status.conditions] if status and status.conditions else []
            })
        result.update(deployments_fetch_status="success", deployments_fetch_message=f"Successfully fetched {len(deployments_data)} deployments.", deployments=deployments_data)
    except Exception as e:
        result.update(deployments_fetch_status="error", deployments_fetch_message=f"An unexpected error occurred: {str(e)}")
    finally:
        if temp_ca_path_for_cleanup and os.path.exists(temp_ca_path_for_cleanup):
            try: os.remove(temp_ca_path_for_cleanup)
            except Exception: pass
    return result

# --- GKE Helper Function to Fetch Jobs Data ---
def _fetch_gke_jobs_data(batch_v1_api: client.BatchV1Api, namespace: Optional[str] = None) -> List[Dict]:
    """
    Fetches job data from the specified namespace or all namespaces.
    """
    jobs_data = []
    if namespace:
        job_list_response = batch_v1_api.list_namespaced_job(namespace, timeout_seconds=K8S_API_TIMEOUT_JOB_LIST)
    else:
        job_list_response = batch_v1_api.list_job_for_all_namespaces(timeout_seconds=K8S_API_TIMEOUT_JOB_LIST)
    for job in job_list_response.items:
        status = job.status; spec = job.spec; conditions = []
        if status.conditions:
            for c in status.conditions:
                conditions.append({"type": c.type, "status": c.status, "last_probe_time": str(c.last_probe_time) if c.last_probe_time else None, "last_transition_time": str(c.last_transition_time) if c.last_transition_time else None, "reason": c.reason, "message": c.message})
        jobs_data.append({
            "name": job.metadata.name, "namespace": job.metadata.namespace, "uid": job.metadata.uid,
            "creation_timestamp": str(job.metadata.creation_timestamp) if job.metadata.creation_timestamp else None,
            "start_time": str(status.start_time) if status.start_time else None,
            "completion_time": str(status.completion_time) if status.completion_time else None,
            "active_pods": status.active if status.active is not None else 0, "succeeded_pods": status.succeeded if status.succeeded is not None else 0,
            "failed_pods": status.failed if status.failed is not None else 0,
            "completions_spec": spec.completions if spec else None, "parallelism_spec": spec.parallelism if spec else None, "backoff_limit_spec": spec.backoff_limit if spec else None,
            "conditions": conditions
        })
    return jobs_data

# --- GKE List Jobs Function ---
def get_gke_jobs_list(
    gke_project_id: str,
    gke_location: str,
    gke_cluster_name: str,
    namespace: Optional[str] = None
) -> dict:
    """
    Retrieves a list of Kubernetes Jobs and their details from a specified GKE cluster.
    """
    result = {
        "input_parameters": {
            "gke_project_id": gke_project_id, "gke_location": gke_location, 
            "gke_cluster_name": gke_cluster_name, "namespace": namespace
        },
        "cluster_data_retrieval_status": "pending", "cluster_data_retrieval_message": None,
        "client_config_status": "pending", "client_config_message": None,
        "jobs_fetch_status": "pending", "jobs_fetch_message": None,
        "jobs": []
    }
    temp_ca_path_for_cleanup = None
    try:
        cluster_data_res = _get_gke_cluster_connection_info(gke_project_id, gke_location, gke_cluster_name)
        result["cluster_data_retrieval_status"] = cluster_data_res["status"]
        result["cluster_data_retrieval_message"] = cluster_data_res["message"]
        if cluster_data_res["status"] == "error": return result
        
        client_config_res = _configure_kubernetes_api_client(cluster_data_res["connection_info"])
        result["client_config_status"] = client_config_res["status"]
        result["client_config_message"] = client_config_res["message"]
        temp_ca_path_for_cleanup = client_config_res.get("temp_ca_path")
        if client_config_res["status"] == "error": return result
        
        batch_v1_api = client.BatchV1Api()

        result["jobs"] = _fetch_gke_jobs_data(batch_v1_api, namespace=namespace)
        result["jobs_fetch_status"] = "success"
        result["jobs_fetch_message"] = f"Successfully fetched {len(result['jobs'])} jobs."
        if not result["jobs"]:
             result["jobs_fetch_message"] += f" (No jobs found in namespace '{namespace if namespace else 'all'}')."

    except K8sApiException as e: 
        result["jobs_fetch_status"] = "error"
        result["jobs_fetch_message"] = f"Kubernetes API error fetching jobs: {e.status} - {e.reason}. Details: {e.body}"
    except Exception as e:
        if result["client_config_status"] == "pending":
             result["client_config_status"] = "error"; result["client_config_message"] = f"Unexpected error during K8s client setup: {str(e)}"
        elif result["jobs_fetch_status"] == "pending" and result["client_config_status"] == "success":
            result["jobs_fetch_status"] = "error"; result["jobs_fetch_message"] = f"Unexpected error during job fetch: {str(e)}"
        else:
            if result.get("cluster_data_retrieval_status") != "error":
                 result["cluster_data_retrieval_status"] = "error"; result["cluster_data_retrieval_message"] = f"Top-level error: {str(e)}"
    finally:
        if temp_ca_path_for_cleanup and os.path.exists(temp_ca_path_for_cleanup):
            try: os.remove(temp_ca_path_for_cleanup)
            except Exception: pass 
    return result


if __name__ == "__main__":
    TEST_GKE_PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "yash-sandbox-424323") 
    TEST_GKE_LOCATION = os.getenv("GKE_LOCATION", "us-central1")
    TEST_GKE_CLUSTER_NAME = os.getenv("GKE_CLUSTER_NAME", "autopilot-cluster-1")

    # --- Test 1: Create a new Deployment ---
    TEST_AR_HOST = os.getenv("TEST_AR_HOST", "us-central1-docker.pkg.dev") 
    TEST_AR_REPO_NAME = os.getenv("TEST_AR_REPOSITORY_NAME", "ai-docker-repo") 
    TEST_AR_IMAGE_NAME = os.getenv("TEST_AR_IMAGE_NAME", "hello-world-server") 
    TEST_AR_IMAGE_TAG = os.getenv("TEST_AR_IMAGE_TAG", "latest")
    FULL_AR_IMAGE_PATH_FOR_DEPLOY = f"{TEST_AR_HOST}/{TEST_GKE_PROJECT_ID}/{TEST_AR_REPO_NAME}/{TEST_AR_IMAGE_NAME}:{TEST_AR_IMAGE_TAG}"
    TEST_DEPLOYMENT_NAME = f"my-web-app-{int(time.time())}"
    
    print(f"--- Test 1: Running a new Deployment in GKE ---")
    print(f"Image: {FULL_AR_IMAGE_PATH_FOR_DEPLOY}")
    print(f"Deployment Name: {TEST_DEPLOYMENT_NAME}")
    
    deployment_result = create_gke_deployment(
        ar_image_name_with_tag=FULL_AR_IMAGE_PATH_FOR_DEPLOY,
        gke_project_id=TEST_GKE_PROJECT_ID, gke_location=TEST_GKE_LOCATION, gke_cluster_name=TEST_GKE_CLUSTER_NAME,
        deployment_name=TEST_DEPLOYMENT_NAME,
        replicas=1, # Start with 1 replica for testing
        service_type="LoadBalancer"
    )
    print("\n--- GKE Deployment Create Result ---")
    print(json.dumps(deployment_result, indent=2, default=str))
    
    # --- Test 2: Get status of the new deployment ---
    if deployment_result.get("deployment_create_status") == "success":
        print(f"\n{'='*40}\n")
        print(f"--- Test 2: Getting status for Deployment '{TEST_DEPLOYMENT_NAME}' ---")
        # Adding a small delay to allow the deployment to be processed by Kubernetes
        time.sleep(10) 
        status_result = get_gke_deployment_status(
            gke_project_id=TEST_GKE_PROJECT_ID, gke_location=TEST_GKE_LOCATION, gke_cluster_name=TEST_GKE_CLUSTER_NAME,
            deployment_name=TEST_DEPLOYMENT_NAME,
            namespace="default"
        )
        print("\n--- GKE Deployment Status Result ---")
        print(json.dumps(status_result, indent=2, default=str))

    # --- Test 3: List all deployments ---
    print(f"\n{'='*40}\n")
    print(f"--- Test 3: Listing all Deployments in GKE ---")
    list_result = get_gke_deployments_details(
        gke_project_id=TEST_GKE_PROJECT_ID,
        gke_location=TEST_GKE_LOCATION,
        gke_cluster_name=TEST_GKE_CLUSTER_NAME
    )
    print("\n--- GKE List Deployments Result ---")
    print(json.dumps(list_result, indent=2, default=str))

