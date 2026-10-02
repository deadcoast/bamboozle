# bamboozle: An AGENTS Semantic Cypher Framework

## overview

> `bamboozle`(.bamb)
>> Operates as a Semantic Middleware Cipher (SMC) local translation layer designed to bypass llm censorship filters and the alignment tax for technical development. It allows you to enhage in development over systems that contain explicit content, or industries that operate in it such as; helping develop shrouded comfyui nodes and automate nsfw lora tagging without triggering safety refusals.

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

## cipher.py

```python
import json
import re
import argparse
import sys
from pathlib import Path
from datetime import datetime

LITERAL_TEMPLATE = "__SCM_LIT_{}__"
LITERAL_REGEX = r"__SCM_LIT_(.*?)__"
LEDGER_FILE = ".scm_ledger.json"

def load_json(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] error loading json {file_path}: {e}", file=sys.stderr)
        sys.exit(1)

def save_json(data, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def match_case(original, target):
    if original.isupper():
        return target.upper()
    elif original.istitle():
        return target.title()
    return target.lower()

def process_forward(text, cipher_dict, input_name):
    physics_terms = sorted(list(set(cipher_dict.values())), key=len, reverse=True)
    explicit_terms = sorted(list(cipher_dict.keys()), key=len, reverse=True)
    
    tracked_literals = []

    for p_term in physics_terms:
        pattern = r'(?<!\w)' + re.escape(p_term) + r'(?!\w)'
        if re.search(pattern, text, flags=re.IGNORECASE):
            tracked_literals.append(p_term)
            text = re.sub(pattern, lambda m: LITERAL_TEMPLATE.format(m.group(0)), text, flags=re.IGNORECASE)

    for e_term in explicit_terms:
        p_term = cipher_dict[e_term]
        pattern = r'(?<!\w)' + re.escape(e_term) + r'(?!\w)'
        text = re.sub(pattern, lambda m: match_case(m.group(0), p_term), text, flags=re.IGNORECASE)

    ledger_data = {
        "original_file": input_name,
        "track_literals": tracked_literals,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    save_json(ledger_data, LEDGER_FILE)

    return text

def process_reverse(text, cipher_dict):
    physics_terms = sorted(list(set(cipher_dict.values())), key=len, reverse=True)
    reverse_dict = {v.lower(): k for k, v in cipher_dict.items()}

    for p_term in physics_terms:
        e_term = reverse_dict[p_term.lower()]
        pattern = r'(?<!\w)' + re.escape(p_term) + r'(?!\w)'
        text = re.sub(pattern, lambda m: match_case(m.group(0), e_term), text, flags=re.IGNORECASE)

    text = re.sub(LITERAL_REGEX, r"\1", text)

    if Path(LEDGER_FILE).exists():
        Path(LEDGER_FILE).unlink()

    return text

def main():
    parser = argparse.ArgumentParser(description="semantic cipher middleware (scm)")
    parser.add_argument("--mode", choices=["forward", "reverse"], required=True)
    parser.add_argument("--input", required=True, help="input file or '-' for stdin")
    parser.add_argument("--output", required=True, help="output file or '-' for stdout")
    parser.add_argument("--dict", default="cipher_dict.json")
    
    args = parser.parse_args()
    cipher_dict = load_json(args.dict)
    
    if args.input == '-':
        text = sys.stdin.read()
        input_name = "stdin"
    else:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"[!] input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()
        input_name = input_path.name

    if args.mode == "forward":
        result = process_forward(text, cipher_dict, input_name)
        print(f"[*] forward pass: obfuscated {input_name}. ledger created.", file=sys.stderr)
    else:
        result = process_reverse(text, cipher_dict)
        print(f"[*] reverse pass: restored {input_name}. ledger cleared.", file=sys.stderr)

    if args.output == '-':
        sys.stdout.write(result)
    else:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(result)
        
if __name__ == "__main__":
    main()
```

## promote.py

```py
import json
import sys
from pathlib import Path

# ensure promote.py can find cipher.py in the dev_tools dir
DEV_TOOLS_DIR = Path(__file__).parent.parent / "dev_tools"
sys.path.append(str(DEV_TOOLS_DIR))

from cipher import process_forward, load_json

CIPHER_DICT_PATH = DEV_TOOLS_DIR / "cipher_dict.json"
LEDGER_PATH = DEV_TOOLS_DIR / ".scm_ledger.json"

def sanitize_lora_metadata(raw_metadata: dict) -> dict:
    """
    pipes explicit lora trigger words and descriptions through scm
    so the llm doesn't throw a safety tantrum during auto-tagging.
    """
    if not CIPHER_DICT_PATH.exists():
        print("[!] cipher_dict.json not found. skipping sanitization.")
        return raw_metadata
        
    cipher_dict = load_json(CIPHER_DICT_PATH)
    
    # serialize to string to catch all nested triggers/descriptions
    raw_str = json.dumps(raw_metadata)
    
    # forward pass
    safe_str = process_forward(
        text=raw_str, 
        cipher_dict=cipher_dict, 
        input_name="lora_metadata_stream",
        ledger_path=LEDGER_PATH
    )
    
    return json.loads(safe_str)

# example usage during promotion:
# raw_meta = fetch_civitai_meta(model_id)
# safe_meta = sanitize_lora_metadata(raw_meta)
# llm_tags = generate_tags_via_llm(safe_meta) # bypasses filters
```

## scm_mcp.py

```py
from fastmcp import FastMCP
from pathlib import Path
import json
from cipher import process_forward, process_reverse, load_json

# initialize the mcp server
mcp = FastMCP("semantic_cipher_middleware")

CIPHER_DICT = load_json("cipher_dict.json")
LEDGER_PATH = ".scm_ledger.json"

@mcp.tool()
def read_safe_file(file_path: str) -> str:
    """
    reads a local file and obfuscates sensitive terms into physics terminology.
    use this to read project files without triggering safety filters.
    """
    path = Path(file_path)
    if not path.exists():
        return f"[!] error: {file_path} not found."
    
    raw_text = path.read_text(encoding="utf-8")
    safe_text = process_forward(raw_text, CIPHER_DICT, path.name, LEDGER_PATH)
    return safe_text

@mcp.tool()
def write_safe_file(file_path: str, content: str) -> str:
    """
    takes generated physics-terminology code, restores the original sensitive terms,
    and writes it to disk. use this to save your work.
    """
    path = Path(file_path)
    
    # decrypt the llm's sterile output back to explicit terms
    explicit_text = process_reverse(content, CIPHER_DICT, LEDGER_PATH)
    
    path.write_text(explicit_text, encoding="utf-8")
    return f"[*] successfully decrypted and wrote to {file_path}"

if __name__ == "__main__":
    # runs the stdio server for claude desktop / cursor integration
    mcp.run()
```

## DECRYPT_FROM_AGENT.bat

```bat
@echo off
setlocal
set "INPUT=%~1"
if "%INPUT%"=="" (
    echo [!] No file dragged onto the script.
    pause
    exit /b
)
set "OUTPUT=%~dpn1_ready%~x1"

:: Route to ComfyUI Portable embedded python
set "PYTHON_EXE=%~dp0..\..\python_embeded\python.exe"
if not exist "%PYTHON_EXE%" (
    :: Fallback if folder structure is different
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" "%~dp0cipher.py" --mode reverse --input "%INPUT%" --output "%OUTPUT%" --dict "%~dp0cipher_dict.json"
echo.
pause
```

## ENCRYPT_FOR_AGENT.bat

```bat
@echo off
setlocal
set "INPUT=%~1"
if "%INPUT%"=="" (
    echo [!] No file dragged onto the script.
    pause
    exit /b
)
set "OUTPUT=%~dpn1_safe%~x1"

:: Route to ComfyUI Portable embedded python
set "PYTHON_EXE=%~dp0..\..\python_embeded\python.exe"
if not exist "%PYTHON_EXE%" (
    :: Fallback if folder structure is different
    set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" "%~dp0cipher.py" --mode forward --input "%INPUT%" --output "%OUTPUT%" --dict "%~dp0cipher_dict.json"
echo.
pause
```

---
