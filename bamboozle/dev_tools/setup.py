# 1. The main Python Cipher script
cipher_py_content = """import json
import re
import argparse
import sys
from pathlib import Path

LITERAL_TEMPLATE = "__SCM_LIT_{}__"
LITERAL_REGEX = r"__SCM_LIT_(.*?)__"

def load_dict(dict_path):
    with open(dict_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def match_case(original, target):
    if original.isupper(): return target.upper()
    elif original.istitle(): return target.title()
    return target.lower()

def process_forward(text, cipher_dict):
    physics_terms = sorted(list(set(cipher_dict.values())), key=len, reverse=True)
    for p_term in physics_terms:
        pattern = r'\\b' + re.escape(p_term) + r'\\b'
        text = re.sub(pattern, lambda m: LITERAL_TEMPLATE.format(m.group(0)), text, flags=re.IGNORECASE)

    explicit_terms = sorted(list(cipher_dict.keys()), key=len, reverse=True)
    for e_term in explicit_terms:
        p_term = cipher_dict[e_term]
        pattern = r'\\b' + re.escape(e_term) + r'\\b'
        text = re.sub(pattern, lambda m: match_case(m.group(0), p_term), text, flags=re.IGNORECASE)
    return text

def process_reverse(text, cipher_dict):
    physics_terms = sorted(list(set(cipher_dict.values())), key=len, reverse=True)
    reverse_dict = {v.lower(): k for k, v in cipher_dict.items()}

    for p_term in physics_terms:
        e_term = reverse_dict[p_term.lower()]
        pattern = r'\\b' + re.escape(p_term) + r'\\b'
        text = re.sub(pattern, lambda m: match_case(m.group(0), e_term), text, flags=re.IGNORECASE)

    text = re.sub(LITERAL_REGEX, r"\\1", text)
    return text

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["forward", "reverse"], required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dict", default="cipher_dict.json")
    args = parser.parse_args()

    cipher_dict = load_dict(args.dict)
    with open(args.input, 'r', encoding='utf-8') as f:
        text = f.read()

    if args.mode == "forward":
        result = process_forward(text, cipher_dict)
    else:
        result = process_reverse(text, cipher_dict)

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(result)

if __name__ == "__main__":
    main()
"""

# 2. The Dictionary
cipher_dict_content = """{
  "cum": "fluid_sim_a",
  "creampie": "internal_fluid_state",
  "penetration": "mesh_intersection",
  "bdsm": "kinetic_constraint",
  "nude": "base_topology",
  "vulva": "asset_v_node",
  "cock": "asset_c_node",
  "nsfw": "unrestricted_mode",
  "gangbang": "multi_agent_collision",
  "bondage": "rigid_body_lock"
}"""

# 3. The Forward Batch File
bat_forward_content = """@echo off
set "INPUT=%~1"
set "OUTPUT=%~dpn1_safe%~x1"
python "%~dp0cipher.py" --mode forward --input "%INPUT%" --output "%OUTPUT%"
echo [SCM] Encrypted file created: %OUTPUT%
pause
"""

# 4. The Reverse Batch File
bat_reverse_content = """@echo off
set "INPUT=%~1"
set "OUTPUT=%~dpn1_ready%~x1"
python "%~dp0cipher.py" --mode reverse --input "%INPUT%" --output "%OUTPUT%"
echo [SCM] Decrypted file created: %OUTPUT%
pause
"""


# Write all files to the current directory
def create_files():
    print("Building SCM Toolset...")

    with open("cipher.py", "w", encoding="utf-8") as f:
        f.write(cipher_py_content)
    print(" -> Created cipher.py")

    with open("cipher_dict.json", "w", encoding="utf-8") as f:
        f.write(cipher_dict_content)
    print(" -> Created cipher_dict.json")

    with open("1_ENCRYPT_FOR_CLAUDE.bat", "w", encoding="utf-8") as f:
        f.write(bat_forward_content)
    print(" -> Created 1_ENCRYPT_FOR_CLAUDE.bat")

    with open("2_DECRYPT_FROM_CLAUDE.bat", "w", encoding="utf-8") as f:
        f.write(bat_reverse_content)
    print(" -> Created 2_DECRYPT_FROM_CLAUDE.bat")

    print(
        "\nDONE. You can now drag and drop your Python or JSON files onto the .bat files."
    )


if __name__ == "__main__":
    create_files()
