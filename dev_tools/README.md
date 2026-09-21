# Semantic Cipher Middleware (SCM) - Early Development Guide

## Overview

Semantic Cipher Middleware (SCM) is a local translation layer designed to bypass LLM censorship filters (e.g., Claude, ChatGPT). It allows you to develop explicit ComfyUI nodes without triggering safety refusals.

By mapping vulgar or restricted terms (e.g., `creampie`, `gangbang`, `cum`) to sterile physics and rendering terminology (e.g., `internal_fluid_state`, `multi_agent_collision`, `fluid_sim_a`), you can safely feed your source code to an LLM for optimization, debugging, or feature generation, and then reliably decrypt the response back to your raw, explicit codebase.

## Directory Structure

Ensure your `dev_tools` directory is structured exactly as follows within your ComfyUI Portable installation:

```text
D:\AI\ComfyUI_portable\workshop\dev_tools\
├── cipher_dict.json
├── cipher.py
├── 1_ENCRYPT_FOR_CLAUDE.bat
├── 2_DECRYPT_FROM_CLAUDE.bat
└── README.md
```

## Environment & Package Management (uv)

We use `uv` for dependency management due to its speed and efficiency. The base SCM script relies entirely on Python standard libraries, so no external dependencies are required for the cipher itself.

However, when you begin expanding your custom ComfyUI nodes and need external packages (like `numpy`, `torch`, etc.), use `uv` to install them directly into ComfyUI's isolated environment.

To install a package using `uv`, point it to the embedded executable:

```bash
uv pip install <package_name> --python ..\..\python_embeded\python.exe
```

## The Development Loop

Follow this strict three-step loop when utilizing an LLM to generate or modify your node logic.

### 1. Forward Pass (Encrypt)

1. Locate your working script containing your explicit variables and functions.
2. Drag and drop the file onto `1_ENCRYPT_FOR_CLAUDE.bat`.
3. The script will output a sanitized file and generate a `.scm_ledger.json` file. This ledger tracks native physics terms already present in your code to prevent collisions during decryption.

### 2. LLM Processing

1. Open the newly generated `_safe.py` file. All restricted terms will be replaced with their mapped physics equivalents.
2. Copy and paste this sanitized code into your LLM of choice.
3. Prompt the LLM to modify, fix, or expand the code as needed. It will process the logic without throwing safety errors.

### 3. Reverse Pass (Decrypt)

1. Save the LLM's generated response as a Python file (you can overwrite the `_safe.py` file).
2. Drag and drop this file onto `2_DECRYPT_FROM_CLAUDE.bat`.
3. The script will output a `_ready.py` file with all explicit terminology perfectly restored, and it will automatically delete the `.scm_ledger.json` file.
4. Move the `_ready.py` file into your ComfyUI `custom_nodes` directory for testing.

## Dictionary Maintenance (`cipher_dict.json`)

If you need to introduce new explicit terms to your workflow, you must update the dictionary.

**Critical Rules:**

1. **Strict 1:1 Mapping:** No two explicit words can map to the same physics term. If corrupted, the reverse pass will guess wrong and permanently corrupt your variables.
2. **Valid JSON Formatting:** Ensure standard JSON syntax. Do not leave trailing commas.
3. **Lowercase Base:** All keys and values in the dictionary must be lowercase. The `cipher.py` engine dynamically handles uppercase and title-case matching during execution.

## Troubleshooting

- **Batch files crashing instantly:** The `.bat` files use relative paths to locate `..\..\python_embeded\python.exe`. If you move the `dev_tools` folder to a different directory depth, you must edit the `.bat` files and update the `PYTHON_EXE` path, otherwise Windows will fail to execute the script.

---
