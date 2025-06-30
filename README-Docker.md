# Vietnam Hearts Scheduler - Docker Deployment Guide

This guide covers how to build, deploy, and run the Vietnam Hearts Scheduler application using Docker and Google Container Registry (GCR).

## Prerequisites

1. **Docker** - Install Docker Desktop or Docker Engine
2. **Google Cloud SDK** - For GCR operations
3. **Google Cloud Project** - With Container Registry enabled

## Quick Start

### 1. Setup Configuration

```bash
# Copy environment template
cp env.template .env

# Edit .env with your actual values
nano .env

# Create secrets directory and add Google credentials
mkdir -p secrets
# Add your google_credentials.json to secrets/
```

### 2. Build and Run Locally

```bash
# Build the image
./docker.sh build

# Run the container
./docker.sh run

# Or run in background
./docker.sh run -d
```

### 3. Deploy to Google Container Registry

```bash
# Update GCR_PROJECT_ID in docker.sh
nano docker.sh

# Deploy to GCR
./docker.sh deploy v1.1.1
```

## Docker Script Commands

The `docker.sh` script provides comprehensive Docker management:

### Basic Operations

```bash
# Build image
./docker.sh build [TAG]           # Default: latest

# Run container
./docker.sh run [OPTIONS]         # See options below
./docker.sh run -p 9000          # Run on port 9000
./docker.sh run -d               # Run in background
./docker.sh run --rm             # Remove container when stopped

# Stop container
./docker.sh stop

# View logs
./docker.sh logs

# Open shell in container
./docker.sh shell
```

### GCR Operations

```bash
# Push to Google Container Registry
./docker.sh push [TAG]           # Default: latest

# Pull from Google Container Registry
./docker.sh pull [TAG]           # Default: latest

# Full deployment workflow
./docker.sh deploy [TAG]         # Build + Push
```

### Utility Commands

```bash
# Setup Docker network
./docker.sh setup

# Clean up containers and images
./docker.sh clean

# Show help
./docker.sh help
```

## Docker Compose

For local development with additional services:

```bash
# Start application only
docker-compose up

# Start with PostgreSQL database
docker-compose --profile database up

# Run in background
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

## Configuration

### Environment Variables

The application uses environment variables for configuration. Key variables:

- `DATABASE_URL` - Database connection string
- `GOOGLE_APPLICATION_CREDENTIALS` - Path to Google service account JSON
- `ENVIRONMENT` - development/production
- `PORT` - Application port (default: 8080)

### Google Cloud Setup

1. **Enable Container Registry API**:
   ```bash
   gcloud services enable containerregistry.googleapis.com
   ```

2. **Authenticate with Google Cloud**:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

3. **Configure Docker for GCR**:
   ```bash
   gcloud auth configure-docker
   ```

4. **Update docker.sh**:
   ```bash
   # Edit GCR_PROJECT_ID in docker.sh
   GCR_PROJECT_ID="your-actual-project-id"
   ```

## Production Deployment

### 1. Build Production Image

```bash
# Build with version tag
./docker.sh build v1.1.1
```

### 2. Push to GCR

```bash
# Push to registry
./docker.sh push v1.1.1
```

### 3. Deploy to Cloud Run (Optional)

```bash
# Deploy to Cloud Run
gcloud run deploy vietnam-hearts \
  --image gcr.io/YOUR_PROJECT_ID/vietnam-hearts-scheduler:v1.1.1 \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080
```

## Troubleshooting

### Common Issues

1. **Permission Denied**:
   ```bash
   chmod +x docker.sh
   ```

2. **Google Cloud Not Configured**:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```

3. **Container Won't Start**:
   ```bash
   # Check logs
   ./docker.sh logs
   
   # Check environment file
   ls -la .env
   ```

4. **Port Already in Use**:
   ```bash
   # Use different port
   ./docker.sh run -p 9000
   ```

### Health Checks

The application includes health checks:

```bash
# Check container health
docker ps

# Manual health check
curl http://localhost:8080/public/health
```

## Security Considerations

1. **Never commit secrets** - Use `.env` files and volume mounts
2. **Use non-root user** - Container runs as `app` user
3. **Limit container resources** - Set memory and CPU limits in production
4. **Regular updates** - Keep base images updated

## Monitoring

### Logs

```bash
# View application logs
./docker.sh logs

# Follow logs in real-time
docker logs -f vietnam-hearts-container
```

### Metrics

The application exposes health endpoints:
- `/public/health` - Basic health check
- `/docs` - API documentation (development only)

## Backup and Recovery

### Database Backup

```bash
# If using PostgreSQL with docker-compose
docker exec vietnam-hearts-postgres pg_dump -U vietnam_hearts vietnam_hearts > backup.sql
```

### Configuration Backup

```bash
# Backup environment configuration
cp .env .env.backup
cp -r secrets/ secrets.backup/
```

## Support

For issues related to:
- **Docker**: Check Docker documentation
- **Google Cloud**: Check GCP documentation
- **Application**: Check the main README.md

## Version History

- **v1.1.1** - Current version with Docker support
- **v1.1.0** - Added admin API and scheduler
- **v1.0.0** - Initial release 