#!/bin/bash

# Vietnam Hearts Scheduler - Run Script
# This script provides various ways to run the application

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
ENV_FILE=".env"
PORT="8080"
ENVIRONMENT="development"
DRY_RUN="false"
HOST="0.0.0.0"
WORKERS="1"
RELOAD="true"

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
    echo "Vietnam Hearts Scheduler - Run Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  -e, --env-file FILE     Use specified environment file (default: .env)"
    echo "  -p, --port PORT         Set port number (default: 8080)"
    echo "  -H, --host HOST         Set host address (default: 0.0.0.0)"
    echo "  -w, --workers N         Number of worker processes (default: 1)"
    echo "  -E, --environment ENV   Set environment (development/production, default: development)"
    echo "  -d, --dry-run           Enable dry run mode"
    echo "  -r, --reload            Enable auto-reload (default: true in development)"
    echo "  -n, --no-reload         Disable auto-reload"
    echo "  -i, --install           Install dependencies before running"
    echo "  -c, --check             Check configuration and dependencies"
    echo "  -h, --help              Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                                    # Run with default settings"
    echo "  $0 -e .env.production                 # Use production env file"
    echo "  $0 -p 9000 -E production              # Run on port 9000 in production"
    echo "  $0 -d                                 # Run in dry-run mode"
    echo "  $0 -i                                 # Install dependencies and run"
    echo "  $0 -c                                 # Check configuration only"
    echo ""
}

# Function to check if Python and Poetry are installed
check_dependencies() {
    print_status "Checking dependencies..."
    
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 is not installed"
        exit 1
    fi
    
    if ! command -v poetry &> /dev/null; then
        print_error "Poetry is not installed. Please install it first:"
        echo "  curl -sSL https://install.python-poetry.org | python3 -"
        exit 1
    fi
    
    print_success "Dependencies check passed"
}

# Function to install dependencies
install_dependencies() {
    print_status "Installing dependencies with Poetry..."
    poetry install
    print_success "Dependencies installed successfully"
}

# Function to check configuration
check_configuration() {
    print_status "Checking configuration..."
    
    # Check if .env file exists
    if [ ! -f "$ENV_FILE" ]; then
        print_warning "Environment file '$ENV_FILE' not found"
        if [ -f "env.template" ]; then
            print_status "Found env.template. You can copy it to create your .env file:"
            echo "  cp env.template .env"
            echo "  # Then edit .env with your actual values"
        fi
    else
        print_success "Environment file '$ENV_FILE' found"
    fi
    
    # Check if secrets directory exists
    if [ ! -d "secrets" ]; then
        print_warning "secrets/ directory not found"
        print_status "Creating secrets directory..."
        mkdir -p secrets
    fi
    
    # Check if Google credentials file exists
    if [ ! -f "secrets/google_credentials.json" ]; then
        print_warning "Google credentials file not found at secrets/google_credentials.json"
        print_status "You'll need to:"
        echo "  1. Create a service account in Google Cloud Console"
        echo "  2. Download the credentials JSON file"
        echo "  3. Place it at secrets/google_credentials.json"
    else
        print_success "Google credentials file found"
    fi
    
    # Check if templates directory exists
    if [ ! -d "templates" ]; then
        print_error "templates/ directory not found"
        exit 1
    fi
    
    print_success "Configuration check completed"
}

# Function to validate environment file
validate_env_file() {
    if [ -f "$ENV_FILE" ]; then
        print_status "Validating environment file '$ENV_FILE'..."
        
        # Check for required variables in production
        if grep -q "ENVIRONMENT=production" "$ENV_FILE"; then
            print_status "Production environment detected, checking required variables..."
            
            required_vars=(
                "GMAIL_APP_PASSWORD"
                "SCHEDULE_SIGNUP_LINK"
                "EMAIL_PREFERENCES_LINK"
                "INVITE_LINK_FACEBOOK_MESSENGER"
                "INVITE_LINK_DISCORD"
                "ONBOARDING_GUIDE_LINK"
                "INSTAGRAM_LINK"
                "FACEBOOK_PAGE_LINK"
                "NEW_SIGNUPS_RESPONSES_LINK"
            )
            
            missing_vars=()
            for var in "${required_vars[@]}"; do
                if ! grep -q "^${var}=" "$ENV_FILE"; then
                    missing_vars+=("$var")
                fi
            done
            
            if [ ${#missing_vars[@]} -gt 0 ]; then
                print_warning "Missing required variables for production:"
                for var in "${missing_vars[@]}"; do
                    echo "  - $var"
                done
            else
                print_success "All required production variables found"
            fi
        fi
    fi
}

# Function to run the application
run_application() {
    print_status "Starting Vietnam Hearts Scheduler..."
    
    # Source the environment file if it exists FIRST, before printing status
    if [ -f "$ENV_FILE" ]; then
        print_status "Loading environment from '$ENV_FILE'..."
        set -a  # automatically export all variables
        source "$ENV_FILE"
        set +a  # turn off automatic export
    fi
    
    # Now print status after loading environment variables
    print_status "Environment: $ENVIRONMENT"
    print_status "Port: $PORT"
    print_status "Host: $HOST"
    print_status "Dry Run: $DRY_RUN"
    print_status "Auto-reload: $RELOAD"
    print_status "Workers: $WORKERS"
    
    # Set environment variables (these will override .env file values if specified via command line)
    export ENV_FILE="$ENV_FILE"
    export PORT="$PORT"
    export ENVIRONMENT="$ENVIRONMENT"
    export DRY_RUN="$DRY_RUN"
    
    # Build uvicorn command
    UVICORN_CMD="poetry run uvicorn app.main:app --host $HOST --port $PORT"
    
    # Add workers for production
    if [ "$ENVIRONMENT" = "production" ]; then
        UVICORN_CMD="$UVICORN_CMD --workers $WORKERS"
        RELOAD="false"
    fi
    
    # Add reload flag
    if [ "$RELOAD" = "true" ]; then
        UVICORN_CMD="$UVICORN_CMD --reload"
    fi
    
    print_status "Running: $UVICORN_CMD"
    echo ""
    
    # Run the application
    eval "$UVICORN_CMD"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -e|--env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -H|--host)
            HOST="$2"
            shift 2
            ;;
        -w|--workers)
            WORKERS="$2"
            shift 2
            ;;
        -E|--environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        -d|--dry-run)
            DRY_RUN="true"
            shift
            ;;
        -r|--reload)
            RELOAD="true"
            shift
            ;;
        -n|--no-reload)
            RELOAD="false"
            shift
            ;;
        -i|--install)
            INSTALL_DEPS="true"
            shift
            ;;
        -c|--check)
            CHECK_ONLY="true"
            shift
            ;;
        -h|--help)
            show_usage
            exit 0
            ;;
        *)
            print_error "Unknown option: $1"
            show_usage
            exit 1
            ;;
    esac
done

# Main execution
main() {
    echo "🚀 Vietnam Hearts Scheduler"
    echo "=========================="
    echo ""
    
    # Check dependencies
    check_dependencies
    
    # Install dependencies if requested
    if [ "$INSTALL_DEPS" = "true" ]; then
        install_dependencies
    fi
    
    # Check configuration
    check_configuration
    
    # Validate environment file
    validate_env_file
    
    # Exit if only checking
    if [ "$CHECK_ONLY" = "true" ]; then
        print_success "Configuration check completed successfully"
        exit 0
    fi
    
    # Run the application
    run_application
}

# Run main function
main "$@" 