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

# Check if Python 3 is installed
print_status "Checking Python 3..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    print_success "Found $PYTHON_VERSION"
else
    print_error "Python 3 is not installed. Please install Python 3.12 or later."
    exit 1
fi

# Check if Poetry is installed
print_status "Checking Poetry..."
if command -v poetry &> /dev/null; then
    POETRY_VERSION=$(poetry --version)
    print_success "Found $POETRY_VERSION"
    
    # Check Poetry version
    POETRY_VERSION_NUM=$(poetry --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    POETRY_MAJOR=$(echo $POETRY_VERSION_NUM | cut -d. -f1)
    POETRY_MINOR=$(echo $POETRY_VERSION_NUM | cut -d. -f2)
    
    if [ "$POETRY_MAJOR" -eq 1 ] && [ "$POETRY_MINOR" -lt 2 ]; then
        print_warning "You have Poetry $POETRY_VERSION_NUM installed. This version is compatible but older."
        print_status "For the best experience, consider upgrading to Poetry 1.2.0 or later:"
        echo "  poetry self update"
        echo ""
    fi
else
    print_warning "Poetry is not installed. Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
    print_success "Poetry installed successfully"
    
    # Add Poetry to PATH for current session
    export PATH="$HOME/.local/bin:$PATH"
fi

# Install dependencies
print_status "Installing Python dependencies..."
if poetry install; then
    print_success "Dependencies installed successfully"
else
    print_error "Failed to install dependencies. This might be due to Poetry version compatibility."
    print_status "Trying to install without dev dependencies..."
    if poetry install --no-dev; then
        print_success "Core dependencies installed successfully (dev dependencies skipped)"
        print_warning "To install dev dependencies, upgrade Poetry: poetry self update"
    else
        print_error "Failed to install dependencies. Please check your Poetry installation."
        exit 1
    fi
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
print_status "Happy scheduling! 🎉" 