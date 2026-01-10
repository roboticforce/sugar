# Sugar Docker Image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js and npm for Claude CLI
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs

# Install Claude CLI with retry logic and cache clearing
RUN npm cache clean --force && \
    npm install -g @anthropic-ai/claude-code --no-cache || \
    (sleep 5 && npm install -g @anthropic-ai/claude-code --registry https://registry.npmjs.org/)

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Install Sugar in development mode
RUN pip install -e .

# Create a non-root user
RUN useradd --create-home --shell /bin/bash sugar
RUN chown -R sugar:sugar /app
USER sugar

# Create directory for Sugar projects
RUN mkdir -p /home/sugar/projects

# Set working directory for projects
WORKDIR /home/sugar/projects

# Expose port for potential web interface
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD sugar --version || exit 1

# Default command - provides interactive shell for project setup
# 
# USAGE:
# 1. Mount your project: docker run -v /path/to/project:/home/sugar/projects/my-project -it sugar
# 2. Navigate to project: cd my-project  
# 3. Initialize Sugar: sugar init
# 4. Configure agents in .sugar/config.yaml (optional)
# 5. Start autonomous development: sugar run
#
# For Claude agent integration, configure your available agents in .sugar/config.yaml:
#   claude:
#     enable_agents: true
#     available_agents: ["tech-lead", "code-reviewer", "my-custom-agent"]
CMD ["bash"]

# Labels
# Get version dynamically from pyproject.toml during build
ARG VERSION
ENV SUGAR_VERSION=${VERSION}

LABEL org.opencontainers.image.title="🍰 Sugar" \
      org.opencontainers.image.description="🍰 Sugar - The autonomous layer for AI coding agents" \
      org.opencontainers.image.url="https://github.com/roboticforce/sugar" \
      org.opencontainers.image.source="https://github.com/roboticforce/sugar" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.authors="Steven Leggett <contact@roboticforce.io>"