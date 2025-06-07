gcloud beta container --project "yash-sandbox-424323" \
clusters create-auto "autopilot-cluster-1" --region "us-central1" \
--release-channel "regular" --tier "standard" --enable-ip-access\
--no-enable-google-cloud-access --network "projects/yash-sandbox-424323/global/networks/default" \
--subnetwork "projects/yash-sandbox-424323/regions/us-central1/subnetworks/default" \
--cluster-ipv4-cidr "/17" --binauthz-evaluation-mode=DISABLED