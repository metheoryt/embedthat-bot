# Run recipes in PowerShell on Windows (just defaults to `sh`)
set windows-shell := ["pwsh", "-NoProfile", "-Command"]

# List available recipes
default:
    @just --list

# Push git changes and build & push the bot image to the registry
deploy:
    git push
    docker compose build --push bot
