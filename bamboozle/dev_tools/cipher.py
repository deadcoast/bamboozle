import json
import re
import argparse
import sys
from pathlib import Path
from datetime import datetime

LITERAL_TEMPLATE = "__SCM_LIT_{}__"
LITERAL_REGEX = r"__SCM_LIT_(.*?)__"

def load_json(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] error loading json {file_path}: {e}")
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

def process_forward(text, cipher_dict, input_name, ledger_path=".scm_ledger.json"):
    physics_terms = sorted(list(set(cipher_dict.values())), key=len, reverse=True)
    explicit_terms = sorted(list(cipher_dict.keys()), key=len, reverse=True)
    
    tracked_literals = []

    for p_term in physics_terms:
        pattern = r'\b' + re.escape(p_term) + r'\b'
        if re.search(pattern, text, flags=re.IGNORECASE):
            tracked_literals.append(p_term)
            text = re.sub(pattern, lambda m: LITERAL_TEMPLATE.format(m.group(0)), text, flags=re.IGNORECASE)

    for e_term in explicit_terms:
        p_term = cipher_dict[e_term]
        pattern = r'\b' + re.escape(e_term) + r'\b'
        text = re.sub(pattern, lambda m: match_case(m.group(0), p_term), text, flags=re.IGNORECASE)

    ledger_data = {
        "original_file": input_name,
        "track_literals": tracked_literals,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }
    save_json(ledger_data, ledger_path)

    return text

def process_reverse(text, cipher_dict, ledger_path=".scm_ledger.json"):
    physics_terms = sorted(list(set(cipher_dict.values())), key=len, reverse=True)
    reverse_dict = {v.lower(): k for k, v in cipher_dict.items()}

    for p_term in physics_terms:
        e_term = reverse_dict[p_term.lower()]
        pattern = r'\b' + re.escape(p_term) + r'\b'
        text = re.sub(pattern, lambda m: match_case(m.group(0), e_term), text, flags=re.IGNORECASE)

    text = re.sub(LITERAL_REGEX, r"\1", text)

    if Path(ledger_path).exists():
        Path(ledger_path).unlink()

    return text

def main():
    parser = argparse.ArgumentParser(description="semantic cipher middleware (scm)")
    parser.add_argument("--mode", choices=["forward", "reverse"], required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dict", default="cipher_dict.json")
    parser.add_argument("--ledger", default=".scm_ledger.json")
    
    args = parser.parse_args()
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"[!] input file not found: {args.input}")
        sys.exit(1)

    cipher_dict = load_json(args.dict)
    
    with open(input_path, 'r', encoding='utf-8') as f:
        text = f.read()

    if args.mode == "forward":
        result = process_forward(text, cipher_dict, input_path.name, args.ledger)
        print(f"[*] forward pass: obfuscated {input_path.name}. ledger created.")
    else:
        result = process_reverse(text, cipher_dict, args.ledger)
        print(f"[*] reverse pass: restored {input_path.name}. ledger cleared.")

    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(result)
        
if __name__ == "__main__":
    main()