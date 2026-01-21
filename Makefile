.PHONY: help install install-dev install-build clean clean-all build build-gui build-daemon build-release build-debug test lint format check run deps-update deps-show

# Variables
PYTHON := python3
POETRY := poetry
PROJECT_NAME := Perpetua
BUILD_DIR := .build
GUI_DIR := src-gui
SRC_DIR := src

# Colors for output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color


lock:  ## Generate or update poetry.lock
	$(POETRY) lock

# Default target
help: ## Show this help message
	@echo "$(BLUE)$(PROJECT_NAME) - Makefile Commands$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

## Installation targets
install: lock ## Install runtime dependencies only
	@echo "$(BLUE)Installing runtime dependencies...$(NC)"
	$(POETRY) install --only main
	@echo "$(GREEN)Runtime dependencies installed$(NC)"

install-dev: lock ## Install dev dependencies
	@echo "$(BLUE)Installing dev dependencies...$(NC)"
	$(POETRY) install --only dev
	@echo "$(GREEN)Dev dependencies installed$(NC)"

install-build: lock ## Install runtime and build dependencies
	@echo "$(BLUE)Installing runtime and build dependencies...$(NC)"
	$(POETRY) install
	@echo "$(GREEN)Build dependencies installed$(NC)"

## Build targets
build: install-build ## Build both GUI and daemon (release mode)
	@echo "$(BLUE)Building $(PROJECT_NAME)...$(NC)"
	$(PYTHON) build.py
	@echo "$(GREEN)Build completed$(NC)"

build-gui: install-build ## Build GUI only
	@echo "$(BLUE)Building GUI...$(NC)"
	$(PYTHON) build.py --skip-daemon
	@echo "$(GREEN)GUI build completed$(NC)"

build-daemon: install-build ## Build daemon only
	@echo "$(BLUE)Building daemon...$(NC)"
	$(PYTHON) build.py --skip-gui
	@echo "$(GREEN)Daemon build completed$(NC)"

build-release: install-build ## Build in release mode with clean
	@echo "$(BLUE)Building $(PROJECT_NAME) (release mode)...$(NC)"
	$(PYTHON) build.py --clean
	@echo "$(GREEN)Release build completed$(NC)"

build-debug: install-build ## Build in debug mode
	@echo "$(BLUE)Building $(PROJECT_NAME) (debug mode)...$(NC)"
	$(PYTHON) build.py --debug
	@echo "$(GREEN)Debug build completed$(NC)"

## Clean targets
clean: ## Clean build artifacts
	@echo "$(BLUE)Cleaning build artifacts...$(NC)"
	rm -rf $(BUILD_DIR)
	rm -rf $(GUI_DIR)/dist
	rm -rf $(SRC_DIR)/__pycache__
	@echo "$(GREEN)Build artifacts cleaned$(NC)"

clean-all: clean ## Clean all artifacts including Poetry cache and node_modules
	@echo "$(BLUE)Cleaning all artifacts...$(NC)"
	rm -rf $(GUI_DIR)/src-tauri/target
	rm -rf $(GUI_DIR)/node_modules
	$(POETRY) cache clear pypi --all -n 2>/dev/null || true
	@echo "$(GREEN)All artifacts cleaned$(NC)"

## Development targets
test: ## Run tests
	@echo "$(BLUE)Running tests...$(NC)"
	$(POETRY) run pytest
	@echo "$(GREEN)Tests completed$(NC)"

test-verbose: ## Run tests with verbose output
	@echo "$(BLUE)Running tests (verbose)...$(NC)"
	$(POETRY) run pytest -v
	@echo "$(GREEN)Tests completed$(NC)"

lint: install-dev ## Run linter (ruff)
	@echo "$(BLUE)Running linter...$(NC)"
	$(POETRY) run ruff check $(SRC_DIR)
	@echo "$(GREEN)Linting completed$(NC)"

lint-fix: install-dev ## Run linter and fix issues
	@echo "$(BLUE"Running linter and fixing issues...$(NC)"
	$(POETRY) run ruff check --fix $(SRC_DIR)
	@echo "$(GREEN)Linting and fixing completed$(NC)"

format: install-dev ## Format code with ruff
	@echo "$(BLUE)Formatting code...$(NC)"
	$(POETRY) run ruff format $(SRC_DIR)
	$(POETRY) run ruff check --fix $(SRC_DIR)
	@echo "$(GREEN)Code formatted$(NC)"

check: lint test ## Run linter and tests
	@echo "$(GREEN)All checks passed$(NC)"


## GUI specific targets
gui-install: ## Install GUI dependencies (npm)
	@echo "$(BLUE)Installing GUI dependencies...$(NC)"
	cd $(GUI_DIR) && npm install
	@echo "$(GREEN)GUI dependencies installed$(NC)"

gui-dev: ## Run GUI in development mode
	@echo "$(BLUE)Starting GUI development server...$(NC)"
	cd $(GUI_DIR) && cargo tauri dev
	@echo "$(GREEN)GUI development server stopped$(NC)"

## Info targets
info: ## Show project information
	@echo "$(BLUE)Project Information:$(NC)"
	@echo "  Name:    $(PROJECT_NAME)"
	@echo "  Python:  $$($(PYTHON) --version)"
	@echo "  Poetry:  $$($(POETRY) --version)"
	@echo "  Build:   $(BUILD_DIR)"
	@echo ""
	@echo "$(BLUE)Directories:$(NC)"
	@ls -lh | grep "^d" || true

version: ## Show version
	@echo "$(BLUE)$(PROJECT_NAME) Version:$(NC)"
	@grep '^version' pyproject.toml | head -1 | cut -d'"' -f2
