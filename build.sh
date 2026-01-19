#!/bin/bash

# Complete build script for pyContinuity
# This script builds both GUI and Python daemon

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color

# Project directories
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
GUI_DIR="$SCRIPT_DIR/src-gui"
BUILD_DIR="$SCRIPT_DIR/.build"

# Parse arguments
SKIP_GUI=false
SKIP_DAEMON=false
CLEAN=false
DEBUG=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-gui)
            SKIP_GUI=true
            shift
            ;;
        --skip-daemon)
            SKIP_DAEMON=true
            shift
            ;;
        --clean)
            CLEAN=true
            shift
            ;;
        --debug)
            DEBUG=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --skip-gui      Skip GUI build"
            echo "  --skip-daemon   Skip daemon build"
            echo "  --clean         Clean build artifacts before building"
            echo "  --debug         Build in debug mode (default is release)"
            echo "  -h, --help      Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Print step
print_step() {
    echo -e "\n${PURPLE}======================================${NC}"
    echo -e "${PURPLE}$1${NC}"
    echo -e "${PURPLE}======================================${NC}\n"
}

# Print success
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# Print error
print_error() {
    echo -e "${RED}✗ $1${NC}" >&2
}

# Print warning
print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Clean build artifacts
clean_build() {
    print_step "Cleaning build artifacts"

    if [ -d "$BUILD_DIR" ]; then
        print_warning "Removing $BUILD_DIR"
        rm -rf "$BUILD_DIR"
    fi

    if [ -d "$GUI_DIR/dist" ]; then
        print_warning "Removing $GUI_DIR/dist"
        rm -rf "$GUI_DIR/dist"
    fi

    print_success "Build artifacts cleaned"
}

# Build GUI
build_gui() {
    print_step "Building GUI (Tauri + React)"

    if [ "$SKIP_GUI" = true ]; then
        print_warning "Skipping GUI build"
        return
    fi

    # Check for npm
    if ! command -v npm &> /dev/null; then
        print_error "npm not found. Please install Node.js and npm."
        exit 1
    fi

    # Check for cargo
    if ! command -v cargo &> /dev/null; then
        print_error "cargo not found. Please install Rust and cargo."
        exit 1
    fi

    # Install dependencies if needed
    if [ ! -d "$GUI_DIR/node_modules" ]; then
        print_step "Installing npm dependencies"
        cd "$GUI_DIR"
        npm install
        cd "$SCRIPT_DIR"
    fi

    # Build GUI
    cd "$GUI_DIR"
    if [ "$DEBUG" = true ]; then
        npm run tauri build -- --debug
    else
        npm run tauri build
    fi
    cd "$SCRIPT_DIR"

    print_success "GUI build completed"
}

# Build daemon
build_daemon() {
    print_step "Building Python daemon with Nuitka"

    if [ "$SKIP_DAEMON" = true ]; then
        print_warning "Skipping daemon build"
        return
    fi

    # Check for python
    if ! command -v python3 &> /dev/null; then
        print_error "python3 not found"
        exit 1
    fi

    # Check for Nuitka
    if ! python3 -m nuitka --version &> /dev/null; then
        print_warning "Nuitka not found. Installing..."
        python3 -m pip install nuitka
    fi

    # Determine build type
    BUILD_TYPE="release"
    if [ "$DEBUG" = true ]; then
        BUILD_TYPE="debug"
    fi

    # Build with Nuitka
    NUITKA_CMD=(
        python3 -m nuitka
        --standalone
        --output-dir="$BUILD_DIR"
        --enable-plugin=multiprocessing
        --include-package=src
        --include-package=service
        --include-package=config
        --include-package=network
        --include-package=utils
        --include-package=event
        --include-package=input
        --include-package=model
        --include-package=command
        --include-data-dir="$SCRIPT_DIR/src=src"
        --follow-imports
        --assume-yes-for-downloads
    )

    # Add macOS-specific options
    if [[ "$OSTYPE" == "darwin"* ]]; then
        NUITKA_CMD+=(
            --macos-create-app-bundle
            --macos-app-name="pyContinuity Launcher"
            --macos-app-icon="$SCRIPT_DIR/logo/logo.icns"
        )
    else
        NUITKA_CMD+=(--onefile)
    fi

    # Add optimization flags for release
    if [ "$DEBUG" = false ]; then
        NUITKA_CMD+=(
            --lto=yes
            --remove-output
        )
    fi

    NUITKA_CMD+=("$SCRIPT_DIR/launcher.py")

    "${NUITKA_CMD[@]}"

    print_success "Daemon build completed"
}

# Copy GUI to build directory
copy_gui_to_build() {
    print_step "Copying GUI executable to build directory"

    if [ "$SKIP_GUI" = true ]; then
        print_warning "Skipping GUI copy (GUI build was skipped)"
        return
    fi

    BUILD_TYPE="release"
    if [ "$DEBUG" = true ]; then
        BUILD_TYPE="debug"
    fi

    TAURI_TARGET="$GUI_DIR/src-tauri/target"

    # macOS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        SRC_BUNDLE="$TAURI_TARGET/$BUILD_TYPE/bundle/macos/perpetua.app"
        if [ -d "$SRC_BUNDLE" ]; then
            # Find launcher app
            LAUNCHER_APP="$BUILD_DIR/launcher.app"
            if [ ! -d "$LAUNCHER_APP" ]; then
                LAUNCHER_APP="$BUILD_DIR/pyContinuity Launcher.app"
            fi

            if [ -d "$LAUNCHER_APP" ]; then
                DEST="$LAUNCHER_APP/Contents/MacOS/perpetua.app"
                mkdir -p "$(dirname "$DEST")"
                cp -R "$SRC_BUNDLE" "$DEST"
                print_success "Copied GUI bundle to: $DEST"
            else
                print_warning "Launcher app bundle not found, copying to build root"
                DEST="$BUILD_DIR/perpetua.app"
                cp -R "$SRC_BUNDLE" "$DEST"
                print_success "Copied GUI bundle to: $DEST"
            fi
        else
            print_warning "GUI app bundle not found at: $SRC_BUNDLE"
        fi
    else
        # Linux/Windows
        EXE_NAME="perpetua"
        SRC_EXE="$TAURI_TARGET/$BUILD_TYPE/$EXE_NAME"

        if [ -f "$SRC_EXE" ]; then
            DEST="$BUILD_DIR/$EXE_NAME"
            cp "$SRC_EXE" "$DEST"
            chmod +x "$DEST"
            print_success "Copied GUI executable to: $DEST"
        else
            print_warning "GUI executable not found at: $SRC_EXE"
        fi
    fi
}

# Print summary
print_summary() {
    print_step "Build Summary"

    echo "Platform: $(uname -s)"
    if [ "$DEBUG" = true ]; then
        echo "Build type: Debug"
    else
        echo "Build type: Release"
    fi
    echo "Build directory: $BUILD_DIR"
    echo ""

    if [ -d "$BUILD_DIR" ]; then
        echo "Build artifacts:"
        ls -lh "$BUILD_DIR" | tail -n +2 | awk '{printf "  - %s (%s)\n", $9, $5}'
    fi
}

# Main build process
main() {
    echo -e "\n${BLUE}pyContinuity Build System${NC}"
    echo -e "${BLUE}Platform: $(uname -s)${NC}\n"

    # Clean if requested
    if [ "$CLEAN" = true ]; then
        clean_build
    fi

    # Build GUI
    if [ "$SKIP_GUI" = false ]; then
        build_gui
    fi

    # Build daemon
    if [ "$SKIP_DAEMON" = false ]; then
        build_daemon
    fi

    # Copy GUI to build
    if [ "$SKIP_GUI" = false ] && [ "$SKIP_DAEMON" = false ]; then
        copy_gui_to_build
    fi

    # Print summary
    print_summary

    print_success "\n🎉 Build completed successfully!"
}

# Run main
main
