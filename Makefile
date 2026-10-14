# SPDX-License-Identifier: FSL-1.1-ALv2
# Copyright (c) 2025 Delos Data, Inc.
# =============================================================================
# Configuration
# =============================================================================

# Load environment variables from .env file
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

# List of required prerequisites to check
PREREQS = uv kubectl helm
ENV_VARS = REMOTE_USER REMOTE_HOST CLUSTER ANSIBLE_INVENTORY_FILE
FRAMEWORK_DIR := $(CURDIR)
TESTS_DIR := $(shell cd $(FRAMEWORK_DIR)/tests && pwd)

# Git and Qase (Qase uses QASE_TESTOPS_RUN_TITLE env var for run title; default overridable by CI/user)
GIT_COMMIT_HASH ?= (unknown commit hash)
IS_GIT_REPO := $(shell git rev-parse --is-inside-work-tree 2>/dev/null)
ifeq ($(IS_GIT_REPO),true)
GIT_COMMIT_HASH := $(shell git describe --dirty --always | sed 's/.*-g//; s/-dirty$$/ (dirty)/')
endif

QASE_TESTOPS_RUN_TITLE ?= Production test run $(GIT_COMMIT_HASH)
PATH := $(PATH):$(shell uv run dirname `uv python find` 2>/dev/null || true)

export QASE_TESTOPS_RUN_TITLE

# Emojis
OK := ✅
ERR := ❌
TASK := ⏹

# Pytest: extra flags only (pytest reads env PYTEST_ADDOPTS; do not put -m here, use TEST_MARKER)
PYTEST_ADDOPTS ?=
export PYTEST_ADDOPTS
TEST_MARKER ?= "k3s or lgtm or metrics"
CI_JOB_ID ?= "local"

help:
	@echo ""
	@echo ""
	@echo "Production Test Framework"
	@echo "-------------------------"
	@echo ""
	@echo "Infrastructure management targets:"
	@echo "  prereqs                   - Check for any missing pre-requisites"
	@echo "  deploy-helm-charts        - Deploy Helm charts (expects kubectl context configured)"
	@echo "  undeploy-helm-charts      - Remove Helm charts"
	@echo ""
	@echo "Top-level testing targets:"
	@echo "  test                      - deploy-helm -> run all tests (undeploy -> pytest teardown tests)"
	@echo "  test-run-only             - Run tests without deploying Helm charts"
	@echo "  test-production           - Runs the production tests"
	@echo "  test-openmosaic           - Runs the open mosaic integration tests"
	@echo ""
	@echo "Additional targets (building blocks, k3d):"
	@echo "  help                      - Show this help"
	@echo "  help-container-targets     - Shorter test-target summary for container / CI"
	@echo "  run-tests                 - Single pytest run with TEST_MARKER (no undeploy / teardown pass)"
	@echo "  run-all-tests              - run-tests, undeploy helm, then pytest teardown tests"
	@echo "  create-test-cluster       - Create local k3d cluster (CI_JOB_ID-k3s) and mosaic namespace"
	@echo "  destroy-test-cluster      - Delete the k3d cluster from create-test-cluster"
	@echo ""
	@echo "Test options:"
	@echo "  TEST_MARKER='...'         - Pytest marker (default: k3s or lgtm or metrics). Use 'not teardown' for main suite."
	@echo "  PYTEST_ADDOPTS            - Extra command line options to send to pytest"
	@echo "  QASE_TESTOPS_RUN_TITLE    - Qase run title (default: Production test run <commit-hash> [dirty])"
	@echo ""

help-container-targets:
	@echo "Top-level testing (see 'make help' for details):"
	@echo "  test              - deploy-helm and full test + teardown"
	@echo "  test-run-only     - run-tests with 'not teardown' (no deploy)"
	@echo "  test-production   - Runs the production tests"
	@echo "  test-openmosaic   - Runs the open mosaic integration tests"
	@echo ""
	@echo "Test options:"
	@echo "  TEST_MARKER='...'         - Pytest markers (default: k3s or lgtm or metrics). Use 'not teardown' for main suite."
	@echo "  PYTEST_ADDOPTS            - Extra command line options to send to pytest"
	@echo "  QASE_TESTOPS_RUN_TITLE    - Qase run title (default: Production test run <commit-hash> [dirty])"
	@echo ""

# =============================================================================
# Infrastructure Management Targets
# =============================================================================

prereqs:
	@echo "Checking pre-requisites..."
	@for exec in $(PREREQS); do \
		if command -v "$$exec" >/dev/null 2>&1; then \
			echo "    $(OK) $$exec found in PATH"; \
		else \
			echo "    $(ERR) $$exec not found in PATH"; \
		fi; \
	done
	@for env_var in $(ENV_VARS); do \
		if [ -z "$$(printenv $$env_var)" ]; then \
			echo "    $(ERR) $$env_var not set"; \
		else \
			echo "    $(OK) $$env_var is set"; \
		fi; \
	done

deploy-helm-charts: 
	@echo "$(TASK) Deploying Helm charts on cluster $(CLUSTER)..."
	cd /app/framework/charts/mosaic && \
	helm dep update && \
	helm -n mosaic upgrade --install mosaic . --wait --timeout 10m

undeploy-helm-charts: 
	@echo "$(TASK) Removing Helm charts from cluster $(CLUSTER)..."
	@cd /app/framework/charts/mosaic && \	
	kubectl delete ns mosaic
	@echo "$(TASK) Helm charts removed from cluster $(CLUSTER)"

create-test-cluster: 
	@echo creating the kubernetes cluster...
	@k3d cluster create ${CI_JOB_ID}-k3s --api-port localhost:6443 --wait --timeout 10m
	@kubectl create namespace mosaic

destroy-test-cluster: 
	@echo destroying the kubernetes cluster...
	-@sudo k3d cluster delete ${CI_JOB_ID}-k3s

# =============================================================================
# Base Test Targets (used by all test targets)
# =============================================================================

.PHONY: run-all-tests run-tests
.PHONY: test test-run-only test-production test-openmosaic

# Single test run (one marker). QASE_TESTOPS_RUN_TITLE is exported above.
run-tests: prereqs
	@echo "$(TASK) Running tests with marker: $(TEST_MARKER)..."
	@uv run pytest -m $(TEST_MARKER) -v $(TESTS_DIR); \
	exit $$?;

# Full test plan: main tests -> undeploy -> teardown tests (for use cases that deploy)
run-all-tests: run-tests undeploy-helm-charts
	@echo "$(TASK) Running teardown tests..."; 
	@uv run pytest -m teardown -v $(TESTS_DIR); \
	exit $$?;

# -----------------------------------------------------------------------------
# Top-level test targets
# -----------------------------------------------------------------------------

test: prereqs deploy-helm-charts run-all-tests

test-run-only: prereqs
	@$(MAKE) run-tests TEST_MARKER='not teardown'

test-production: create-test-cluster deploy-helm-charts run-tests destroy-test-cluster

test-openmosaic: run-tests

