#! /bin/bash

# Get the project root directory regardless of where the script is called from
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="${SCRIPT_DIR}"
VENV_PATH="${PROJECT_ROOT}/.venv"

# Define colors for different processes
RED='\033[0;31m'
GREEN='\033[0;32m'
MAGENTA='\033[0;35m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Array to store background PIDs
pids=()

# Check if Stripe webhook is configured and CLI is available
STRIPE_WEBHOOK_SECRET_SET=$(grep -E "^STRIPE_WEBHOOK_SECRET=" "${PROJECT_ROOT}/.env" 2>/dev/null | cut -d'=' -f2- | sed 's/^"\(.*\)"$/\1/' | sed "s/^'\(.*\)'$/\1/")
if [ -n "$STRIPE_WEBHOOK_SECRET_SET" ]; then
    if ! command -v stripe &> /dev/null; then
        echo "ERROR: STRIPE_WEBHOOK_SECRET is set in .env but stripe CLI is not available."
        echo "Please install the Stripe CLI: https://stripe.com/docs/stripe-cli"
        exit 1
    fi
fi

# Cleanup function to kill all child processes
cleanup() {
    echo "Shutting down processes..."
    
    # First, terminate child processes individually
    for pid in ${pids[@]}; do
        if kill -0 $pid 2>/dev/null; then
            kill -TERM $pid 2>/dev/null
        fi
    done
    
    # Give processes a moment to terminate gracefully
    sleep 1
    
    # Force kill any remaining processes
    for pid in ${pids[@]}; do
        if kill -0 $pid 2>/dev/null; then
            kill -9 $pid 2>/dev/null
        fi
    done
    
    # Cleanup Next.js server specifically
    cleanup_next_server
    
    echo "Shutdown complete"
    exit 0
}

# Modify trap to only catch specific signals
trap cleanup SIGINT SIGTERM
# Remove EXIT from trap to prevent double-cleanup

# Function to run a process and color its output
run_with_color() {
    local command=$1
    local color=$2
    local name=$3
    local dir=$4
    
    # Run the command with virtual environment activation and optional directory change
    if [ -n "$dir" ]; then
        (cd "$dir" && source "${VENV_PATH}/bin/activate" && $command) 2>&1 | while read -r line; do
            echo -e "${color}[$name] $line${NC}"
        done &
    else
        (source "${VENV_PATH}/bin/activate" && $command) 2>&1 | while read -r line; do
            echo -e "${color}[$name] $line${NC}"
        done &
    fi
    # Store the PID of the last background process
    pids+=($!)
}

cleanup_next_server() {
    kill -9 `ps -ef|grep next-server | awk '{ print $2}'| head -n 1` >/dev/null 2>&1
}
cleanup_uvicorn() {
    kill -9 `ps -ef|grep uvicorn | awk '{ print $2}'| head -n 1` >/dev/null 2>&1
}
cleanup_stripe_listen() {
    kill -9 `ps -ef|grep "stripe listen" | awk '{ print $2}'| head -n 1` >/dev/null 2>&1
}

# Clean up old processes
cleanup_next_server
cleanup_uvicorn
cleanup_stripe_listen
# Run all processes
run_with_color "uvicorn docrouter_app.main:app --reload --host 0.0.0.0 --port 8000" "$RED" "FASTAPI" "packages"
run_with_color "python worker.py" "$GREEN" "WORKER" "packages/worker"
run_with_color "npm run dev" "$MAGENTA" "NEXTJS" "frontend"
run_with_color "npx http-server -p 8080 --cors -c-1" "$BLUE" "HTTP_SERVER" "public"

# Start Stripe webhook listener if configured
if [ -n "$STRIPE_WEBHOOK_SECRET_SET" ]; then
    run_with_color "stripe listen --forward-to localhost:8000/v0/account/payments/webhook" "$CYAN" "STRIPE" ""
fi

# Wait for any process to exit
while true; do
    for pid in ${pids[@]}; do
        if ! kill -0 $pid 2>/dev/null; then
            echo "Process $pid has exited. Stopping all processes..."
            cleanup
            exit 0
        fi
    done
    sleep 1
done

