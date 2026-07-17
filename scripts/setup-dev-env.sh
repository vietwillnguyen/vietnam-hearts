#!/bin/bash

# Vietnam Hearts Scheduler - Setup Script
# This script helps you set up the environment for the Vietnam Hearts Scheduler

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

echo "🚀 Vietnam Hearts Scheduler - Setup"
echo "==================================="
echo ""

# Check if uv is installed (uv manages Python itself, so no separate
# python3 check is needed - it downloads the version in .python-version)
print_status "Checking uv..."
if command -v uv &> /dev/null; then
    UV_VERSION=$(uv --version)
    print_success "Found $UV_VERSION"
else
    print_warning "uv is not installed. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    print_success "uv installed successfully"

    # Add uv to PATH for current session
    export PATH="$HOME/.local/bin:$PATH"
fi

# Install dependencies
print_status "Installing Python dependencies..."
if uv sync; then
    print_success "Dependencies installed successfully"
else
    print_error "Failed to install dependencies. Please check your uv installation."
    exit 1
fi

# Create secrets directory
print_status "Creating secrets directory..."
mkdir -p secrets
print_success "Secrets directory created"

# Check if .env file exists
if [ ! -f ".env" ]; then
    print_status "Creating .env file from template..."
    if [ -f "env.template" ]; then
        cp env.template .env
        print_success ".env file created from template"
        print_warning "Please edit .env file with your actual configuration values"
    else
        print_error "env.template not found. Please create a .env file manually."
        exit 1
    fi
else
    print_success ".env file already exists"
fi

# Check for Google credentials
if [ ! -f "secrets/google_credentials.json" ]; then
    print_warning "Google credentials file not found"
    echo ""
    print_status "To set up Google Sheets integration, you need to:"
    echo "1. Go to Google Cloud Console (https://console.cloud.google.com/)"
    echo "2. Create a new project or select an existing one"
    echo "3. Enable the Google Sheets API"
    echo "4. Create a Service Account"
    echo "5. Download the JSON credentials file"
    echo "6. Place it at: secrets/google_credentials.json"
    echo ""
    print_status "For detailed instructions, see the README.md file"
else
    print_success "Google credentials file found"
fi

# Check configuration
print_status "Running configuration check..."
if ./run.sh --check; then
    print_success "Configuration check passed"
else
    print_warning "Configuration check had issues. Please review the warnings above."
fi

echo ""
print_success "Setup completed successfully!"
echo ""
print_status "Next steps:"
echo "1. Edit the .env file with your actual configuration values"
echo "2. Set up Google Sheets integration (if not already done)"
echo "3. Run the application with: ./run.sh"
echo "4. For help, run: ./run.sh --help"
echo ""
print_status "Happy developing! 🎉" 