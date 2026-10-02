import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

LITERAL_TEMPLATE = "__SCM_LIT_{}__"
LITERAL_REGEX = r"__SCM_LIT_(.*?)__"
LEDGER_FILE = ".scm_ledger.json"


class Bamboozletranslate:
    def __init__(self, dictionary: dict[str, str]):
        self.dictionary = dictionary
        self.physics_terms = sorted(
            list(set(dictionary.values())), key=len, reverse=True
        )
        self.explicit_terms = sorted(list(dictionary.keys()), key=len, reverse=True)

    def match_case(self, original: str, target: str) -> str:
        if original.isupper():
            return target.upper()
        elif original.istitle():
            return target.title()
        return target.lower()

    def forward(self, text: str) -> str:
        tracked_literals = []

        for p_term in self.physics_terms:
            pattern = r"(?<!\w)" + re.escape(p_term) + r"(?!\w)"
            if re.search(pattern, text, flags=re.IGNORECASE):
                tracked_literals.append(p_term)
                text = re.sub(
                    pattern,
                    lambda m: f"__SCM_LIT_{m.group(0)}__",
                    text,
                    flags=re.IGNORECASE,
                )

        for e_term in self.explicit_terms:
            p_term = self.dictionary[e_term]
            pattern = r"(?<!\w)" + re.escape(e_term) + r"(?!\w)"
            text = re.sub(
                pattern,
                lambda m: self.match_case(m.group(0), p_term),
                text,
                flags=re.IGNORECASE,
            )

        return text

    def reverse(self, text: str) -> str:
        reverse_dict = {v.lower(): k for k, v in self.dictionary.items()}

        for p_term in self.physics_terms:
            e_term = reverse_dict[p_term.lower()]
            pattern = r"(?<!\w)" + re.escape(p_term) + r"(?!\w)"
            text = re.sub(
                pattern,
                lambda m: self.match_case(m.group(0), e_term),
                text,
                flags=re.IGNORECASE,
            )

        text = re.sub(r"__SCM_LIT_(.*?)__", r"\1", text)

        return text


def load_json(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[!] error loading json {file_path}: {e}", file=sys.stderr)
        sys.exit(1)


def save_json(data, file_path):
    with open(file_path, "w", encoding="utf-8") as f:
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
        pattern = r"(?<!\w)" + re.escape(p_term) + r"(?!\w)"
        if re.search(pattern, text, flags=re.IGNORECASE):
            tracked_literals.append(p_term)
            text = re.sub(
                pattern,
                lambda m: LITERAL_TEMPLATE.format(m.group(0)),
                text,
                flags=re.IGNORECASE,
            )

    for e_term in explicit_terms:
        p_term = cipher_dict[e_term]
        pattern = r"(?<!\w)" + re.escape(e_term) + r"(?!\w)"
        text = re.sub(
            pattern, lambda m: match_case(m.group(0), p_term), text, flags=re.IGNORECASE
        )

    ledger_data = {
        "original_file": input_name,
        "track_literals": tracked_literals,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    save_json(ledger_data, LEDGER_FILE)

    return text


def process_reverse(text, cipher_dict):
    physics_terms = sorted(list(set(cipher_dict.values())), key=len, reverse=True)
    reverse_dict = {v.lower(): k for k, v in cipher_dict.items()}

    for p_term in physics_terms:
        e_term = reverse_dict[p_term.lower()]
        pattern = r"(?<!\w)" + re.escape(p_term) + r"(?!\w)"
        text = re.sub(
            pattern, lambda m: match_case(m.group(0), e_term), text, flags=re.IGNORECASE
        )

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

    if args.input == "-":
        text = sys.stdin.read()
        input_name = "stdin"
    else:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"[!] input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        with open(input_path, "r", encoding="utf-8") as f:
            text = f.read()
        input_name = input_path.name

    if args.mode == "forward":
        result = process_forward(text, cipher_dict, input_name)
        print(
            f"[*] forward pass: obfuscated {input_name}. ledger created.",
            file=sys.stderr,
        )
    else:
        result = process_reverse(text, cipher_dict)
        print(
            f"[*] reverse pass: restored {input_name}. ledger cleared.", file=sys.stderr
        )

    if args.output == "-":
        sys.stdout.write(result)
    else:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result)


if __name__ == "__main__":
    main()
