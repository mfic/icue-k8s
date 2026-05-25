set windows-shell := ["bash", "-c"]

widget_name := "k8s"
dist_dir    := "dist"

# Show available recipes
default:
    @just --list

# Start dev server + backend
dev:
    docker compose up --detach
    @echo "Dev server:  http://localhost:8888"
    @echo "K8s backend: http://localhost:9090"

# Start only the widget dev server (no backend)
dev-ui:
    docker compose up devserver --detach
    @echo "Dev server: http://localhost:8888"

# Start only the backend
backend:
    docker compose up backend --detach
    @echo "K8s backend: http://localhost:9090"

# Stop all services
stop:
    docker compose down

# Stream backend logs
logs:
    docker compose logs -f backend

# Package with the native icuewidget CLI
build:
    mkdir -p {{dist_dir}}
    icuewidget build

# Package into .icuewidget via Docker (no CLI needed)
package:
    mkdir -p {{dist_dir}}
    docker run --rm \
        -v "{{justfile_directory()}}/src:/widget" \
        -v "{{justfile_directory()}}/{{dist_dir}}:/output" \
        -e WIDGET_NAME={{widget_name}} \
        icue-packager:latest

# Package and open in iCUE
install: package
    explorer.exe "$(wslpath -w {{justfile_directory()}}/{{dist_dir}}/{{widget_name}}.icuewidget)"

# Remove build output and stop containers
clean:
    rm -rf {{dist_dir}}
    docker compose down --remove-orphans
