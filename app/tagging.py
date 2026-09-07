"""Tags and people.

This is where the site stands or falls: **there is no AI**. The site knows who
is in a photograph only because somebody said so. "Show me the pictures of Max"
works exactly as far as somebody has tagged -- so tagging has to be **fast**,
not pretty.

Which is why this module does three things:

* **In bulk**, never one photograph at a time. An assignment takes a list of
  ids.
* **A "recently used" list** of people. The same family comes up over and over
  in one session; the keys `1`-`9` map onto it, which turns 200 clicks into 20
  keystrokes.
* **Nothing is created twice.** Names are normalised, and a name that already
  exists is reused.
"""
import json
import logging
import re
import unicodedata

from . import db

log = logging.getLogger("family")

RECENT_KEY = "recent_people"
RECENT_MAX = 9          # as many as there are digits on a keyboard

_SPACE = re.compile(r"\s+")


def clean(name: str) -> str:
    """Clean a name so that " Max  " and "Max" are the same thing."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    name = _SPACE.sub(" ", name)
    return name[:60]


# ---------------------------------------------------------------------------
#  Names -- tags and people run through the same machinery
# ---------------------------------------------------------------------------
_TAB = {"tag": ("tags", "photo_tags", "tag_id"),
        "person": ("people", "photo_people", "person_id")}


def _tables(kind_: str):
    try:
        return _TAB[kind_]
    except KeyError:
        raise ValueError("kind must be tag or person")


def ensure(kind_: str, name: str) -> int:
    """The id of a name -- created when it does not exist yet."""
    tab, _, _ = _tables(kind_)
    name = clean(name)
    if not name:
        raise ValueError("the name is missing")
    row = db.connect().execute(
        f"SELECT id FROM {tab} WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row["id"]
    with db.tx() as c:
        return c.execute(f"INSERT INTO {tab} (name) VALUES (?)", (name,)).lastrowid


def all_of(kind_: str) -> list:
    """Every name, with how many photographs -- in the order that matters: what
    is used often comes first, not what happens to start with an A."""
    tab, link, column = _tables(kind_)
    return [dict(r) for r in db.connect().execute(
        f"SELECT t.id, t.name, COUNT(l.photo_id) AS n "
        f"FROM {tab} t LEFT JOIN {link} l ON l.{column}=t.id "
        f"GROUP BY t.id ORDER BY n DESC, t.name")]


def rename(kind_: str, ident: int, name: str) -> dict:
    """Rename. If the new name already exists the two are merged -- which is
    the case that actually happens: "Max" and "max" side by side."""
    tab, link, column = _tables(kind_)
    name = clean(name)
    if not name:
        raise ValueError("the name is missing")
    other_one = db.connect().execute(
        f"SELECT id FROM {tab} WHERE name=? COLLATE NOCASE AND id<>?",
        (name, ident)).fetchone()
    with db.tx() as c:
        if other_one:
            c.execute(f"UPDATE OR IGNORE {link} SET {column}=? WHERE {column}=?",
                      (other_one["id"], ident))
            c.execute(f"DELETE FROM {link} WHERE {column}=?", (ident,))
            c.execute(f"DELETE FROM {tab} WHERE id=?", (ident,))
            _forget(kind_, ident)
            return {"merged_into": other_one["id"], "name": name}
        c.execute(f"UPDATE {tab} SET name=? WHERE id=?", (name, ident))
    return {"id": ident, "name": name}


def forget(kind_: str, ident: int) -> dict:
    """Remove a name. The photographs stay -- only the assignment goes."""
    tab, link, column = _tables(kind_)
    with db.tx() as c:
        n = c.execute(f"SELECT COUNT(*) FROM {link} WHERE {column}=?", (ident,)).fetchone()[0]
        c.execute(f"DELETE FROM {link} WHERE {column}=?", (ident,))
        c.execute(f"DELETE FROM {tab} WHERE id=?", (ident,))
    _forget(kind_, ident)
    return {"removed": ident, "was_on": n}


# ---------------------------------------------------------------------------
#  Assigning -- always in bulk
# ---------------------------------------------------------------------------
def assign(kind_: str, name_or_id, photo_ids, on: bool = True) -> dict:
    """Put a tag or a person on a selection (or take it off).

    `INSERT OR IGNORE`: a photograph that already has the tag is not an error,
    it is the normal case -- somebody drags across the same row twice.
    """
    tab, link, column = _tables(kind_)
    ids = [int(i) for i in (photo_ids or [])]
    if not ids:
        raise ValueError("no photographs selected")
    ident = (int(name_or_id) if str(name_or_id).isdigit()
             else ensure(kind_, str(name_or_id)))
    with db.tx() as c:
        if on:
            c.executemany(
                f"INSERT OR IGNORE INTO {link} (photo_id, {column}) VALUES (?,?)",
                [(i, ident) for i in ids])
        else:
            q = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM {link} WHERE {column}=? AND photo_id IN ({q})",
                      (ident, *ids))
    if kind_ == "person" and on:
        remember(ident)
    name = db.connect().execute(f"SELECT name FROM {tab} WHERE id=?",
                                (ident,)).fetchone()["name"]
    return {"id": ident, "name": name, "photos": len(ids), "on": on}


def of_photos(kind_: str, photo_ids) -> dict:
    """`{photo_id: [names]}` for a selection -- one query, not one per photograph."""
    tab, link, column = _tables(kind_)
    ids = [int(i) for i in (photo_ids or [])]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out = {i: [] for i in ids}
    for r in db.connect().execute(
            f"SELECT l.photo_id, t.name FROM {link} l JOIN {tab} t ON t.id=l.{column} "
            f"WHERE l.photo_id IN ({q}) ORDER BY t.name", ids):
        out[r["photo_id"]].append(r["name"])
    return out


# ---------------------------------------------------------------------------
#  "Recently used" -- what the keys 1-9 carry
# ---------------------------------------------------------------------------
def _recent_ids() -> list:
    try:
        return [int(i) for i in json.loads(db.get_state(RECENT_KEY) or "[]")]
    except (ValueError, TypeError):
        return []


def remember(person_id: int) -> None:
    ids = [i for i in _recent_ids() if i != person_id]
    ids.insert(0, int(person_id))
    db.set_state(RECENT_KEY, json.dumps(ids[:RECENT_MAX]))


def _forget(kind_: str, ident: int) -> None:
    if kind_ != "person":
        return
    ids = [i for i in _recent_ids() if i != ident]
    db.set_state(RECENT_KEY, json.dumps(ids))


def recent_people() -> list:
    """The people sitting on keys 1-9.

    When the list is not full yet -- a first session, or somebody was deleted
    -- it is topped up with the most frequent ones. That way every key carries
    something from the first second.
    """
    ids = _recent_ids()
    every_one = {p["id"]: p for p in all_of("person")}
    out = [every_one[i] for i in ids if i in every_one]
    for p in sorted(every_one.values(), key=lambda p: (-p["n"], p["name"])):
        if len(out) >= RECENT_MAX:
            break
        if p["id"] not in {q["id"] for q in out}:
            out.append(p)
    return out[:RECENT_MAX]


# ---------------------------------------------------------------------------
#  A default list to start from
# ---------------------------------------------------------------------------
# ⚠ These are **suggestions**, not rows in the database. A tag is only created
# when it actually sits on a photograph -- otherwise after a day there would be
# forty tags, three of them used, and the number next to a name would mean
# nothing.
#
# Grouped, because a list of forty words in no order is a list you cannot find
# anything in. The order inside each group is the order you look for them in.
DEFAULT_TAGS = {
    "Occasions": [
        "Birthday", "Christmas", "New Year", "Easter", "Wedding", "Baptism",
        "Communion", "Graduation", "Anniversary", "Funeral", "Carnival",
        "National Day",
    ],
    "Out and about": [
        "Holiday", "Day out", "Weekend", "Walk", "Hiking", "Cycling",
        "Swimming", "Beach", "Mountains", "Camping", "City", "Countryside",
        "Zoo", "Museum", "Restaurant", "Spa",
    ],
    "At home": [
        "Garden", "House", "Cooking", "Party", "Christmas tree", "Snow day",
    ],
    "What is on it": [
        "Family", "Portrait", "Group photo", "Landscape", "Animals", "Pets", "Food",
        "Flowers", "Architecture", "Sunset", "Night", "Snow", "Rain", "Car",
    ],
    "For me": [
        "Favourite", "Print this", "Needs sorting", "Scan", "Old photo",
        "Blurry", "Duplicate",
    ],
}

# Flattened, for comparison
_DEFAULT_FLAT = [n for group in DEFAULT_TAGS.values() for n in group]


def tag_choices() -> dict:
    """What can be clicked: what is already there, and the rest of the list.

    `used` are the tags that exist, with how many photographs -- those come
    first, because they are what this particular library actually uses.
    `suggested` are the defaults that do not exist yet, grouped.
    """
    have = {g["name"].casefold(): g for g in all_of("tag")}
    suggested = {}
    for group, names in DEFAULT_TAGS.items():
        rest = [n for n in names if n.casefold() not in have]
        if rest:
            suggested[group] = rest
    return {"used": list(have.values()), "suggested": suggested,
            "all": sorted({g["name"] for g in have.values()} | set(_DEFAULT_FLAT),
                          key=str.casefold)}


def person_choices() -> dict:
    """What can be clicked as a person.

    `used` are the ones that exist in the library. `suggested` are the **first
    names of the people with an account** that do not exist yet -- the people
    who have an account are also the people in the photographs, so the list
    carries something from the first second.
    """
    have = {p["name"].casefold(): p for p in all_of("person")}
    suggested = []
    try:
        from . import members
        # `pickable()` and not `all_of()`: an administrator account is not a
        # person in a photograph, and "site-admin" in that list is only noise.
        for m in members.pickable():
            first_name = clean((m.get("display_name") or m["username"]).split(" ")[0])
            if first_name and first_name.casefold() not in have and first_name not in suggested:
                suggested.append(first_name)
    except Exception:                                            # noqa: BLE001
        pass
    return {"used": list(have.values()), "suggested": sorted(suggested),
            "all": sorted({p["name"] for p in have.values()} | set(suggested),
                          key=str.casefold)}


def assign_many(kind_: str, names, photo_ids, on: bool = True) -> dict:
    """More than one name at a time.

    Separated by a comma -- and by a semicolon as well, because that is just as
    easy to hit on a keyboard.
    """
    if isinstance(names, str):
        names = re.split(r"[,;]", names)
    clean_, seen_ = [], set()
    for n in names or []:
        n = clean(n)
        if n and n.casefold() not in seen_:
            seen_.add(n.casefold())
            clean_.append(n)
    if not clean_:
        raise ValueError("the name is missing")
    made = [assign(kind_, n, photo_ids, on=on) for n in clean_]
    return {"names": [g["name"] for g in made],
            "photos": made[0]["photos"], "on": on,
            "each": made}
