#!/bin/bash

# Script to manage git operations: clone a new repository or update an existing one's remote URL

# Default Git repository URL
default_url="https://gitlab.power-theory.io:8443/development/powertwin-solver.git"
# Default branch
default_branch="main"

# Function to print usage
print_usage() {
    echo "Usage: $0 [--action <update|clone>] [--token-name <token_name>] [--token-value <token_value>] [--url <git_repository_url>] [--branch <branch_name> (default: main)]"
}

# Initialize variables to check if any flags were provided
flags_provided=false
branch=$default_branch

# Parse command-line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --action) action="$2"; shift ;;
        --token-name) token_name="$2"; shift ;;
        --token-value) token_value="$2"; shift ;;
        --url) url="$2"; shift ;;
        --branch) branch="$2"; shift ;;
        --help) print_usage; exit 0 ;;
        *) echo "Unknown parameter passed: $1"; print_usage; exit 1 ;;
    esac
    shift
    flags_provided=true
done

# If no flags were provided, prompt for missing inputs
if [[ $flags_provided == false ]]; then
    read -p "Do you want to 'update' the remote URL of an existing repository or 'clone' a new repository? Enter 'update' or 'clone': " action
    read -p "Enter Personal Access Token name: " token_name
    read -s -p "Enter Personal Access Token value (input will be hidden): " token_value
    echo  # Move to a new line after the hidden input for better formatting
    read -p "Enter Git repository URL (press Enter to use default: $default_url): " url
    url=${url:-$default_url}
    read -p "Enter branch name to clone (press Enter to use default: $default_branch): " input_branch
    branch=${input_branch:-$default_branch}
else
    # Use default URL if not provided
    url=${url:-$default_url}
fi

# Remove any leading 'https://' from the URL if present
formatted_url="${url#https://}"

# Prepare the URL for git operations
full_url="https://${token_name}:${token_value}@${formatted_url}"

# Check the user's choice and perform the corresponding action
if [[ $action == "clone" ]]; then
    # Clone the repository
    git clone -b $branch $full_url
elif [[ $action == "update" ]]; then
    # Update the remote URL of the repository
    git remote set-url origin $full_url
else
    echo "Invalid option. Please enter 'update' or 'clone'."
    print_usage
    exit 1
fi
