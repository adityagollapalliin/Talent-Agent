"""Alias normalisation (memory/skill_aliases.yaml) and focus-technology matching."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def _key(s: str) -> str:
    s = s.lower().replace("–", "-").replace("&", " and ")
    return re.sub(r"[^a-z0-9+#]+", " ", s).strip()


class Aliases:
    def __init__(self, canonical: dict[str, list[str]], focus_groups: dict[str, list[str]], focus: list[str]):
        self.map: dict[str, str] = {}
        for canon, aliases in canonical.items():
            self.map[_key(canon)] = canon
            for a in aliases or []:
                self.map[_key(a)] = canon
        self.focus = list(focus)
        for f in self.focus:  # focus names are canonical even without an alias entry
            self.map.setdefault(_key(f), f)
        # focus tech -> set of canonical-name keys that count as a hit
        self.focus_members: dict[str, set[str]] = {}
        for f in self.focus:
            members = {_key(self.canonical(f))}
            for m in (focus_groups or {}).get(f, []) or []:
                members.add(_key(self.canonical(m)))
            self.focus_members[f] = members

    @classmethod
    def load(cls, path: Path, focus: list[str]) -> Aliases:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        raw = raw or {}
        return cls(raw.get("canonical") or {}, raw.get("focus_groups") or {}, focus)

    def canonical(self, name: str) -> str:
        name = re.sub(r"\s+", " ", (name or "").strip()).strip(" .;,")
        return self.map.get(_key(name), name)

    def normalise_list(self, items: list[str]) -> list[str]:
        out, seen = [], set()
        for it in items or []:
            c = self.canonical(it)
            k = _key(c)
            if c and k not in seen:
                seen.add(k)
                out.append(c)
        return out

    def focus_hits(self, items: list[str]) -> list[str]:
        keys = {_key(self.canonical(i)) for i in items or []}
        return [f for f in self.focus if self.focus_members[f] & keys]

    def all_names(self) -> list[str]:
        return sorted(set(self.map.values()))
