from alias_pass import Taxonomy, load_prod_triggers, pairwise_overlap
from alias_pass import run as alias_run

tax = Taxonomy.load()
alias_rep = alias_run(load_prod_triggers(MANIFEST), tax)
overlap = pairwise_overlap(alias_rep)

for (a, b), j in overlap.items():
    key = f"{a}|{b}"
    entry = matrix.setdefault(key, {})
    entry["trigger_overlap"] = j
    entry["trigger_conflict"] = j >= 0.5  # tune threshold
