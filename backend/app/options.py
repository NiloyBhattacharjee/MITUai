"""Menu item options ("Size*: Regular, Large +0.80") and checking a customer's choices against them.

One line per group, the same in business.yaml and the dashboard:
    "Size*: Regular, Large +0.80"      pick exactly one (* = required); +0.80 is the extra cost
    "Milk: Full cream, Oat +0.80"      pick at most one
    "Extras (any): Extra shot +0.60"   pick any number
Stored as [{"name", "required", "multi", "choices": [{"name", "price" (cents)}]}].
"""
import re

from .config import cents, money

_LINE = re.compile(r"^\s*(?P<name>[^:*(]+?)\s*(?P<star>\*)?\s*(?P<any>\(any\))?\s*:\s*(?P<choices>.+)$", re.I)
_CHOICE = re.compile(r"^(?P<name>.+?)(?:\s*\+\s*(?P<price>\d+(?:\.\d{1,2})?))?$")


def parse(lines: list[str]) -> list[dict]:
    """Raises ValueError naming the line that's wrong."""
    groups = []
    for line in lines:
        if not line.strip():
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f'"{line}": write it like "Milk: Full cream, Oat +0.80"')
        choices = []
        for part in (c.strip() for c in m["choices"].split(",")):
            c = _CHOICE.match(part)
            if not part or not c:
                raise ValueError(f'"{line}": empty choice')
            choices.append({"name": c["name"].strip(), "price": cents(c["price"] or 0)})
        if m["star"] and m["any"]:
            raise ValueError(f'"{line}": use * (pick one, required) or (any) (pick several), not both')
        groups.append({"name": m["name"].strip(), "required": bool(m["star"]), "multi": bool(m["any"]), "choices": choices})
    return groups


def lines(groups: list[dict]) -> list[str]:
    """The reverse of parse, for editing in the dashboard."""
    def choice(c):
        return c["name"] + (f" +{c['price'] / 100:.2f}" if c["price"] else "")
    return [f"{g['name']}{'*' if g['required'] else ''}{' (any)' if g['multi'] else ''}: {', '.join(map(choice, g['choices']))}"
            for g in groups]


def describe(groups: list[dict]) -> list[str]:
    """For the LLM: what each group allows and what each choice costs."""
    rule = lambda g: "choose any" if g["multi"] else "required, choose one" if g["required"] else "optional, choose one"  # noqa: E731
    return [f"{g['name']} ({rule(g)}): " + ", ".join(c["name"] + (f" (+{money(c['price'])})" if c["price"] else "") for c in g["choices"])
            for g in groups]


def choose(groups: list[dict], picked: list[str]) -> tuple[list[str], int, list[str]]:
    """Match the customer's picks (choice names, any group) to the item's options.
    Returns (picked names as written on the menu, extra cost in cents, problems)."""
    by_name = {}
    for g in groups:
        for c in g["choices"]:
            by_name.setdefault(c["name"].lower(), []).append((g, c))
    chosen, problems = [], []
    for raw in picked or []:
        found = by_name.get(str(raw).strip().lower())
        if not found:
            problems.append(f'"{raw}" isn\'t an option. Options: ' + "; ".join(describe(groups)) if groups else f'"{raw}": this item has no options')
        elif len(found) > 1:
            problems.append(f'"{raw}" is in more than one group: ' + ", ".join(g["name"] for g, _ in found))
        elif found[0] not in chosen:
            chosen.append(found[0])
    for g in groups:
        n = sum(1 for cg, _ in chosen if cg is g)
        if g["required"] and n == 0:
            problems.append(f"{g['name']} is required: " + ", ".join(c["name"] for c in g["choices"]))
        if not g["multi"] and n > 1:
            problems.append(f"{g['name']}: choose only one")
    # menu order, so "Large, Oat" reads the same however the customer said it
    ordered = [(g, c) for g in groups for c in g["choices"] if (g, c) in chosen]
    return [c["name"] for _, c in ordered], sum(c["price"] for _, c in ordered), problems
