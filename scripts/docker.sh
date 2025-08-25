#!/bin/bash

# Vietnam Hearts Scheduler - Docker Management Script
# This script provides Docker operations for building, pushing, pulling, and running the application

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROJECT_NAME="vietnam-hearts-automations"
IMAGE_NAME="vietnam-hearts-automation"
VERSION="v2.0.5"
GCR_PROJECT_ID="refined-vector-457419-n6"  # Change this to your GCP project ID
GCR_REGION="europe-west1"  # Change this to your preferred region
GCR_HOSTNAME="gcr.io"

# Default values
ENV_FILE="../.env"
PORT="8080"
CONTAINER_NAME="vietnam-hearts-container"
NETWORK_NAME="vietnam-hearts-network"

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Vietnam Hearts Scheduler - Docker Management Script"
    echo ""
    echo "Usage: $0 [COMMAND] [OPTIONS]"
    echo ""
    echo "Commands:"
    echo "  build [TAG]           Build Docker image (default tag: latest)"
    echo "  push [TAG]            Push image to Google Container Registry"
    echo "  pull [TAG]            Pull image from Google Container Registry"
    echo "  run [OPTIONS]         Run container locally"
    echo "  stop                  Stop running container"
    echo "  logs                  Show container logs"
    echo "  shell                 Open shell in running container"
    echo "  clean                 Remove containers and images"
    echo "  setup                 Setup Docker network and initial configuration"
    echo "  deploy [TAG]          Build, push, and deploy (production workflow)"
    echo ""
    echo "Options for run command:"
    echo "  -e, --env-file FILE   Use specified environment file (default: .env)"
    echo "  -p, --port PORT       Map container port to host port (default: 8080)"
    echo "  -n, --name NAME       Container name (default: vietnam-hearts-container)"
    echo "  -d, --detach          Run in detached mode"
    echo "  --rm                  Remove container when it stops (default)"
    echo "  --no-rm               Keep container after it stops"
    echo ""
    echo "Examples:"
    echo "  $0 build                    # Build with latest tag"
    echo "  $0 build v1.1.1             # Build with specific version"
    echo "  $0 push v1.1.1              # Push specific version to GCR"
    echo "  $0 run -p 9000              # Run on port 9000"
    echo "  $0 run -d                   # Run in background"
    echo "  $0 deploy v1.1.1            # Full deployment workflow"
    echo ""
}

# Function to check if Docker is installed and running
check_docker() {
    print_status "Checking Docker installation..."
    
    if ! command -v docker &> /dev/null; then
        print_error "Docker is not installed"
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        print_error "Docker is not running. Please start Docker and try again."
        exit 1
    fi
    
    print_success "Docker is installed and running"
}

# Function to check if gcloud is configured
check_gcloud() {
    print_status "Checking Google Cloud configuration..."
    
    if ! command -v gcloud &> /dev/null; then
        print_warning "gcloud CLI is not installed. Install it from: https://cloud.google.com/sdk/docs/install"
        return 1
    fi
    
    if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
        print_warning "Not authenticated with Google Cloud. Run: gcloud auth login"
        return 1
    fi
    
    if ! gcloud config get-value project &> /dev/null; then
        print_warning "No default project set. Run: gcloud config set project YOUR_PROJECT_ID"
        return 1
    fi
    
    print_success "Google Cloud is configured"
    return 0
}

# Function to build Docker image
build_image() {
    local tag=${1:-latest}
    local full_tag="${GCR_HOSTNAME}/${GCR_PROJECT_ID}/${IMAGE_NAME}:${tag}"
    
    print_status "Building Docker image with tag: ${tag}"
    print_status "Full image name: ${full_tag}"
    
    # Check if secrets directory exists
    if [ ! -d "secrets" ]; then
        print_warning "secrets/ directory not found. Creating it..."
        mkdir -p secrets
        print_warning "Please add your Google credentials to secrets/google_credentials.json"
    fi
    
    # Check if templates directory exists
    if [ ! -d "templates" ]; then
        print_error "templates/ directory not found. This is required for the application."
        exit 1
    fi
    
    # Build the image
    docker build -t "${full_tag}" -t "${IMAGE_NAME}:${tag}" .
    
    print_success "Image built successfully"
    print_status "Local tag: ${IMAGE_NAME}:${tag}"
    print_status "GCR tag: ${full_tag}"
}

# Function to push image to Google Container Registry
push_image() {
    local tag=${1:-latest}
    local full_tag="${GCR_HOSTNAME}/${GCR_PROJECT_ID}/${IMAGE_NAME}:${tag}"
    
    print_status "Pushing image to Google Container Registry..."
    print_status "Image: ${full_tag}"
    
    # Check gcloud configuration
    if ! check_gcloud; then
        print_error "Cannot push to GCR without proper Google Cloud configuration"
        exit 1
    fi
    
    # Configure Docker to use gcloud as a credential helper
    gcloud auth configure-docker ${GCR_HOSTNAME} --quiet
    
    # Push the image
    docker push "${full_tag}"
    
    print_success "Image pushed successfully to GCR"
    print_status "Image available at: ${full_tag}"
}

# Function to pull image from Google Container Registry
pull_image() {
    local tag=${1:-latest}
    local full_tag="${GCR_HOSTNAME}/${GCR_PROJECT_ID}/${IMAGE_NAME}:${tag}"
    
    print_status "Pulling image from Google Container Registry..."
    print_status "Image: ${full_tag}"
    
    # Check gcloud configuration
    if ! check_gcloud; then
        print_error "Cannot pull from GCR without proper Google Cloud configuration"
        exit 1
    fi
    
    # Configure Docker to use gcloud as a credential helper
    gcloud auth configure-docker ${GCR_HOSTNAME} --quiet
    
    # Pull the image
    docker pull "${full_tag}"
    
    # Tag it locally for convenience
    docker tag "${full_tag}" "${IMAGE_NAME}:${tag}"
    
    print_success "Image pulled successfully from GCR"
}

# Function to setup Docker network
setup_network() {
    print_status "Setting up Docker network..."
    
    if ! docker network ls | grep -q "${NETWORK_NAME}"; then
        docker network create "${NETWORK_NAME}"
        print_success "Created network: ${NETWORK_NAME}"
    else
        print_status "Network ${NETWORK_NAME} already exists"
    fi
}

# Function to run container
run_container() {
    local env_file="$ENV_FILE"
    local port="$PORT"
    local container_name="$CONTAINER_NAME"
    local detach=false
    local remove=true
    
    # Parse additional options
    while [[ $# -gt 0 ]]; do
        case $1 in
            -e|--env-file)
                env_file="$2"
                shift 2
                ;;
            -p|--port)
                port="$2"
                shift 2
                ;;
            -n|--name)
                container_name="$2"
                shift 2
                ;;
            -d|--detach)
                detach=true
                shift
                ;;
            --rm)
                remove=true
                shift
                ;;
            --no-rm)
                remove=false
                shift
                ;;
            *)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
    
    # Check if environment file exists
    if [ ! -f "$env_file" ]; then
        print_warning "Environment file '$env_file' not found"
        if [ -f "env.template" ]; then
            print_status "Found env.template. You can copy it to create your .env file:"
            echo "  cp env.template .env"
            echo "  # Then edit .env with your actual values"
        fi
    fi
    
    # Setup network if it doesn't exist
    setup_network
    
    # Stop existing container if running
    if docker ps -q -f name="${container_name}" | grep -q .; then
        print_status "Stopping existing container: ${container_name}"
        docker stop "${container_name}"
        docker rm "${container_name}"
    fi
    
    # Build run command
    local run_cmd="docker run"
    
    if [ "$detach" = true ]; then
        run_cmd="$run_cmd -d"
    else
        run_cmd="$run_cmd -it"
    fi
    
    if [ "$remove" = true ]; then
        run_cmd="$run_cmd --rm"
    fi
    
    run_cmd="$run_cmd --name ${container_name}"
    run_cmd="$run_cmd --network ${NETWORK_NAME}"
    run_cmd="$run_cmd -p ${port}:8080"
    
    # Add environment file if it exists
    if [ -f "$env_file" ]; then
        run_cmd="$run_cmd --env-file ${env_file}"
    fi
    
    # Add volume mounts for persistence
    run_cmd="$run_cmd -v $(pwd)/logs:/app/logs"
    run_cmd="$run_cmd -v $(pwd)/secrets:/app/secrets"
    
    # Add the image
    run_cmd="$run_cmd ${IMAGE_NAME}:${VERSION}"
    
    print_status "Running container with command:"
    echo "  $run_cmd"
    
    # Execute the command
    eval "$run_cmd"
    
    if [ "$detach" = true ]; then
        print_success "Container started in background"
        print_status "Container name: ${container_name}"
        print_status "Port: ${port}"
        print_status "View logs with: $0 logs"
    else
        print_success "Container stopped"
    fi
}

# Function to stop container
stop_container() {
    print_status "Stopping container: ${CONTAINER_NAME}"
    
    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        docker stop "${CONTAINER_NAME}"
        docker rm "${CONTAINER_NAME}"
        print_success "Container stopped and removed"
    else
        print_warning "Container ${CONTAINER_NAME} is not running"
    fi
}

# Function to show container logs
show_logs() {
    print_status "Showing logs for container: ${CONTAINER_NAME}"
    
    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        docker logs -f "${CONTAINER_NAME}"
    else
        print_warning "Container ${CONTAINER_NAME} is not running"
    fi
}

# Function to open shell in container
open_shell() {
    print_status "Opening shell in container: ${CONTAINER_NAME}"
    
    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        docker exec -it "${CONTAINER_NAME}" /bin/bash
    else
        print_warning "Container ${CONTAINER_NAME} is not running"
    fi
}

# Function to clean up containers and images
cleanup() {
    print_status "Cleaning up Docker resources..."
    
    # Stop and remove containers
    if docker ps -q -f name="${CONTAINER_NAME}" | grep -q .; then
        print_status "Stopping container: ${CONTAINER_NAME}"
        docker stop "${CONTAINER_NAME}"
        docker rm "${CONTAINER_NAME}"
    fi
    
    # Remove images
    if docker images -q "${IMAGE_NAME}" | grep -q .; then
        print_status "Removing images: ${IMAGE_NAME}"
        docker rmi $(docker images -q "${IMAGE_NAME}")
    fi
    
    # Remove network
    if docker network ls | grep -q "${NETWORK_NAME}"; then
        print_status "Removing network: ${NETWORK_NAME}"
        docker network rm "${NETWORK_NAME}"
    fi
    
    print_success "Cleanup completed"
}

# Function to deploy (build, push, and optionally run)
deploy() {
    local tag=${1:-latest}
    
    print_status "Starting deployment workflow..."
    print_status "Version: ${tag}"
    
    # Build
    build_image "$tag"
    
    # Push
    push_image "$tag"
    
    print_success "Deployment completed successfully"
    print_status "Image is now available in GCR: ${GCR_HOSTNAME}/${GCR_PROJECT_ID}/${IMAGE_NAME}:${tag}"
}

# Main script logic
main() {
    # Check Docker first
    check_docker
    
    # Parse command
    case "${1:-help}" in
        build)
            build_image "$2"
            ;;
        push)
            push_image "$2"
            ;;
        pull)
            pull_image "$2"
            ;;
        run)
            shift
            run_container "$@"
            ;;
        stop)
            stop_container
            ;;
        logs)
            show_logs
            ;;
        shell)
            open_shell
            ;;
        clean)
            cleanup
            ;;
        setup)
            setup_network
            ;;
        deploy)
            deploy "$2"
            ;;
        help|--help|-h)
            show_usage
            ;;
        *)
            print_error "Unknown command: $1"
            show_usage
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@" 