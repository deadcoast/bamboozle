import re
from typing import Dict, List

class Bamboozletranslate:
    def __init__(self, dictionary: Dict[str, str]):
        self.dictionary = dictionary
        self.physics_terms = sorted(list(set(dictionary.values())), key=len, reverse=True)
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
            pattern = r'(?<!\w)' + re.escape(p_term) + r'(?!\w)'
            if re.search(pattern, text, flags=re.IGNORECASE):
                tracked_literals.append(p_term)
                text = re.sub(pattern, lambda m: f"__SCM_LIT_{m.group(0)}__", text, flags=re.IGNORECASE)

        for e_term in self.explicit_terms:
            p_term = self.dictionary[e_term]
            pattern = r'(?<!\w)' + re.escape(e_term) + r'(?!\w)'
            text = re.sub(pattern, lambda m: self.match_case(m.group(0), p_term), text, flags=re.IGNORECASE)

        return text

    def reverse(self, text: str) -> str:
        reverse_dict = {v.lower(): k for k, v in self.dictionary.items()}

        for p_term in self.physics_terms:
            e_term = reverse_dict[p_term.lower()]
            pattern = r'(?<!\w)' + re.escape(p_term) + r'(?!\w)'
            text = re.sub(pattern, lambda m: self.match_case(m.group(0), e_term), text, flags=re.IGNORECASE)

        text = re.sub(r"__SCM_LIT_(.*?)__", r"\1", text)

        return text