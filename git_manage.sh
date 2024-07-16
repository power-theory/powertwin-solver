#!/bin/bash

# Script to manage git operations: clone a new repository or update an existing one's remote URL

# Ask the user for their choice
echo "Do you want to 'update' the remote URL of an existing repository or 'clone' a new repository?"
read -p "Enter 'update' or 'clone': " action

# Read input from the user
read -p "Enter Personal Access Token name: " token_name
read -s -p "Enter Personal Access Token value (input will be hidden): " token_value
echo  # Move to a new line after the hidden input for better formatting
read -p "Enter Git repository URL (e.g., gitlab.power-theory.io:8443/development/powertwin): " url

# Remove any leading 'https://' from the URL if present
formatted_url="${url#https://}"

# Prepare the URL for git operations
full_url="https://${token_name}:${token_value}@${formatted_url}.git"

# Check the user's choice and perform the corresponding action
if [[ $action == "clone" ]]; then
    # Clone the repository
    git clone ${full_url}
elif [[ $action == "update" ]]; then
    # Update the remote URL of the repository
    git remote set-url origin ${full_url}
else
    echo "Invalid option. Please enter 'update' or 'clone'."
fi

echo ${full_url}