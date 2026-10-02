# Bamboozle Semantic Cipher (BSC)

## overview

semantic cipher (bsc) is a local translation layer designed to bypass llm censorship filters and the woke alignment tax. it allows you to develop explicit comfyui nodes and automate nsfw lora tagging without triggering safety refusals.

by mapping vulgar or restricted terms (e.g., `creampie`, `gangbang`) to sterile physics and rendering terminology (e.g., `internal_fluid_state`, `multi_agent_collision`), you can safely feed source code or scraped civitai metadata to an llm. it operates in two modes: a manual drag-and-drop dev loop for node creation, and an automated programmatic import for your `promote.py` lora pipeline.

## directory structure

ensure your workshop is structured so `promote.py` can access the cipher module and dictionary:

```text
d:\ai\comfyui_portable\workshop\
├── dev_tools\
│   ├── cipher_dict.json
│   ├── cipher.py
│   ├── 1_encrypt_for_agent.bat
│   ├── 2_decrypt_from_agent.bat
│   └── readme.md
└── scrape\
    ├── promote.py
    ├── _compatibility_matrix.json
    └── _lora_manifest.json
```

## environment & package management (uv)

we use `uv` for dependency management due to its speed. the base bsc script relies entirely on python standard libraries, so no external dependencies are required for the cipher itself.

when expanding your custom comfyui nodes or `promote.py` logic with external packages (like `numpy`, `pandas`, etc.), use `uv` to install them directly into comfyui's isolated environment:

```bash
uv pip install <package_name> --python ..\..\python_embeded\python.exe
```

## automated pipeline integration (promote.py)

bsc isn't just for manual code translation. `promote.py` imports `cipher.py` directly to sanitize raw civitai metadata during your automated scrape → sandbox → promote sequence.

when `promote.py` fetches a lora with explicit trigger words, it pipes the json string through `process_forward()`. this allows you to send the metadata to an llm for automated tagging, compatibility scoring, and `audit_matrix.py` reconciliation without getting blocked by safety filters. the llm processes the "physics" data, and your script writes the safe tags to `_lora_manifest.json`.

## the manual development loop

for writing custom nodes or python scripts, follow this strict three-step loop.

### 1. forward pass (encrypt)

1. locate your working script containing explicit variables.
2. drag and drop the file onto `1_encrypt_for_agent.bat`.
3. the script outputs a sanitized `_safe.py` file and generates a `.bsc_ledger.json` file. this ledger tracks native physics terms already present in your code to prevent collisions during decryption.

### 2. llm processing

1. open the newly generated `_safe.py` file. all restricted terms are now physics equivalents.
2. paste this sanitized code into your llm of choice.
3. prompt the llm to modify, fix, or expand the code. it will process the logic without throwing safety errors.

### 3. reverse pass (decrypt)

1. save the llm's generated response as a python file (overwrite `_safe.py`).
2. drag and drop this file onto `2_decrypt_from_agent.bat`.
3. the script outputs a `_ready.py` file with all explicit terminology perfectly restored, and automatically deletes the `.bsc_ledger.json` file.
4. move `_ready.py` into your comfyui `custom_nodes` or `workshop` directory.

## dictionary maintenance (`cipher_dict.json`)

if you scrape new extreme fetish categories or need new explicit terms, update the dictionary.

**critical rules:**

> 1. **strict 1:1 mapping:** no two explicit words can map to the same physics term. if corrupted, the reverse pass will guess wrong and permanently corrupt your variables.
> 2. **valid json formatting:** ensure standard json syntax. no trailing commas.
> 3. **lowercase base:** all keys and values must be lowercase. `cipher.py` dynamically handles uppercase and title-case matching during execution.

## troubleshooting

- **batch files crashing instantly:** the `.bat` files use relative paths to locate `..\..\python_embeded\python.exe`. if you move the `dev_tools` folder to a different directory depth, you must edit the `.bat` files and update the `python_exe` path.
- **module not found in promote.py:** if running `promote.py` from the `scrape` dir, ensure you append `sys.path.append(str(path(__file__).parent.parent / "dev_tools"))` so it can find `cipher.py`.

---
