import json
from pathlib import Path
from cipher import process_forward, load_json

# assuming project root is d:\ai\comfyui_portable\workshop\scrape
CIPHER_DICT_PATH = Path("cipher_dict.json")
LEDGER_PATH = Path(".scm_ledger.json")

def sanitize_lora_metadata(raw_metadata: dict) -> dict:
    """
    pipes explicit lora trigger words and descriptions through scm
    so the llm doesn't throw a safety tantrum during auto-tagging.
    """
    if not CIPHER_DICT_PATH.exists():
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