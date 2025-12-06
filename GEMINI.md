# GEMINI.md

## Project Overview

This project is a Python-based autonomous agent framework that utilizes a hybrid Large Language Model (LLM) architecture. It intelligently routes tasks to different LLM tiers—ranging from fast, local models to powerful, API-based models—to optimize for cost, speed, and quality. The system is designed for building and running autonomous agents that can perform a variety of tasks, from simple classification to complex code generation and orchestration.

The core of the project is a sophisticated `llm_router.py` that classifies tasks by complexity and selects the most appropriate LLM from a tiered configuration defined in `llm_config.yaml`. This allows the system to handle simple tasks with fast, free, local models, while reserving expensive API calls for tasks that require high-level reasoning or creative generation.

The framework includes a `swarm_daemon.py` that runs agents to achieve a given objective, and a `swarm_daemon_hybrid_example.py` that demonstrates how to integrate the hybrid LLM routing system. The project also includes tools for cost analysis, monitoring, and a variety of shell scripts for interacting with the system's memory and managing agents.

## Building and Running

The project is primarily Python-based and does not have a formal build process. The main entry points are the various Python scripts and shell commands.

### Key Dependencies

*   Python 3
*   Ollama (for running local LLMs)
*   Python libraries: `pyyaml`, `requests`, `openai` (optional)

### Installation

1.  **Install Ollama:**
    ```bash
    curl -fsSL https://ollama.com/install.sh | sh
    ```

2.  **Download Local LLM Models:**
    ```bash
    ollama pull phi3:mini
    ollama pull llama3:8b
    ollama pull mixtral:8x7b
    ollama pull codestral:22b
    ```

3.  **Install Python Dependencies:**
    ```bash
    pip install pyyaml requests openai
    ```

### Running the Hybrid Daemon Example

The `swarm_daemon_hybrid_example.py` is a good starting point for understanding how the system works.

1.  **Run the daemon with an objective:**
    ```bash
    python swarm_daemon_hybrid_example.py --objective "Classify recent memory entries by topic"
    ```

2.  **Check the status of the daemon:**
    ```bash
    python swarm_daemon_hybrid_example.py --status
    ```

### Testing the LLM Router

You can test the LLM router directly from the command line:

```bash
python llm_router.py --prompt "Write a python function to sort a list of numbers" --action "edit_file" --quality-check
```

## Development Conventions

*   **Configuration:** The system is heavily configured through the `llm_config.yaml` file. This file defines the available LLM tiers, models, and routing rules.
*   **Modularity:** The project is composed of several modular components, including the `llm_router`, `governor`, and various memory management scripts.
*   **Shell Scripts:** A number of `.sh` scripts are used to provide a command-line interface to the system's memory and other features.
*   **Logging:** The system uses the Python `logging` module to provide detailed logs of its operations.
*   **State Management:** The daemon's state is persisted to a JSON file (`daemon_state_hybrid.json`), allowing it to be restarted and continue from where it left off.
*   **Extensibility:** The system is designed to be extensible. New LLM providers and models can be added by updating the `llm_config.yaml` file and, if necessary, adding a new `_call_*` method to the `LLMRouter` class.
