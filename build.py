"""Render the neofetch-style profile card into dark_mode.svg and light_mode.svg.

Standard library only. Environment:
    ACCESS_TOKEN  GitHub token that can read your repositories (private ones included).
                  Without it, the last stats saved in cache/stats.json are reused.
    BIRTHDATE     YYYY-MM-DD, used for the Uptime line. Kept out of the repo on purpose.
"""

import calendar
import datetime
import hashlib
import json
import os
import urllib.request
from html import escape
from pathlib import Path

ROOT = Path(__file__).parent
CACHE = ROOT / "cache" / "stats.json"

WIDTH = 64  # characters on each line of the info column
STATS_LEFT = 35  # characters before the " | " in the two-column stats rows
LINE_HEIGHT = 20
TOP = 30
# The portrait uses a smaller font than the text, for more detail (see tools/ascii.py).
ART_FONT_SIZE = 11
ART_LINE_HEIGHT = 14
ART_X = 15
INFO_X = 425
# Monospace characters are about 0.6em wide, so 16px text needs about 9.7px per character.
CARD_WIDTH = INFO_X + round(WIDTH * 9.7) + 25

THEMES = {
    "dark": {
        "background": "#161b22",
        "text": "#c9d1d9",
        "key": "#ffa657",
        "value": "#a5d6ff",
        "add": "#3fb950",
        "del": "#f85149",
        "dots": "#616e7f",
    },
    "light": {
        "background": "#f6f8fa",
        "text": "#24292f",
        "key": "#953800",
        "value": "#0a3069",
        "add": "#1a7f37",
        "del": "#cf222e",
        "dots": "#c2cfde",
    },
}


# ---------------------------------------------------------------- GitHub stats

def graphql(token, query, variables):
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = json.load(response)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


REPOS_QUERY = """
query($after: String) {
  viewer {
    id
    repositories(first: 100, after: $after, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        nameWithOwner
        owner { login }
        defaultBranchRef { target { ... on Commit { oid } } }
      }
    }
  }
}
"""

HISTORY_QUERY = """
query($owner: String!, $name: String!, $author: ID!, $after: String) {
  repository(owner: $owner, name: $name) {
    defaultBranchRef {
      target {
        ... on Commit {
          history(first: 100, after: $after, author: { id: $author }) {
            pageInfo { hasNextPage endCursor }
            nodes { additions deletions }
          }
        }
      }
    }
  }
}
"""


def repo_history(token, name_with_owner, author_id):
    owner, name = name_with_owner.split("/")
    commits = additions = deletions = 0
    after = None
    while True:
        data = graphql(token, HISTORY_QUERY, {"owner": owner, "name": name, "author": author_id, "after": after})
        history = data["repository"]["defaultBranchRef"]["target"]["history"]
        for node in history["nodes"]:
            commits += 1
            additions += node["additions"]
            deletions += node["deletions"]
        if not history["pageInfo"]["hasNextPage"]:
            return {"commits": commits, "add": additions, "del": deletions}
        after = history["pageInfo"]["endCursor"]


def fetch_stats(token, login, cache):
    # Repository names are hashed so the public cache never reveals private repositories.
    known = cache.get("repos", {})
    fresh = {}
    owned = 0
    after = None
    while True:
        viewer = graphql(token, REPOS_QUERY, {"after": after})["viewer"]
        page = viewer["repositories"]
        for repo in page["nodes"]:
            if repo["owner"]["login"].lower() == login.lower():
                owned += 1
            branch = repo["defaultBranchRef"]
            if not branch:
                continue
            key = hashlib.sha256(repo["nameWithOwner"].encode()).hexdigest()
            oid = branch["target"]["oid"]
            if known.get(key, {}).get("oid") == oid:
                fresh[key] = known[key]
            else:
                fresh[key] = {"oid": oid, **repo_history(token, repo["nameWithOwner"], viewer["id"])}
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    additions = sum(r["add"] for r in fresh.values())
    deletions = sum(r["del"] for r in fresh.values())
    totals = {
        "repos": owned,
        # Every repository with at least one of your commits on its default branch.
        "contributed": sum(1 for r in fresh.values() if r["commits"]),
        "commits": sum(r["commits"] for r in fresh.values()),
        "loc": additions - deletions,
        "add": additions,
        "del": deletions,
    }
    return {"totals": totals, "repos": fresh}


# ---------------------------------------------------------------- uptime

def uptime(birthdate, today):
    born = datetime.date.fromisoformat(birthdate)

    def add_months(n):
        # Same day of the month n months after birth, clamped to the end of shorter months.
        year, month = divmod(born.month - 1 + n, 12)
        year, month = born.year + year, month + 1
        return datetime.date(year, month, min(born.day, calendar.monthrange(year, month)[1]))

    total = (today.year - born.year) * 12 + today.month - born.month
    if add_months(total) > today:
        total -= 1
    years, months = divmod(total, 12)
    days = (today - add_months(total)).days

    def unit(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"

    return f"{unit(years, 'year')}, {unit(months, 'month')}, {unit(days, 'day')}"


# ---------------------------------------------------------------- rendering
# A line is a list of (text, css class or None) segments.

def length(segments):
    return sum(len(text) for text, _ in segments)


def key_segments(key):
    parts = []
    for i, part in enumerate(key.split(".")):
        if i:
            parts.append((".", None))
        parts.append((part, "key"))
    return parts


def dotted(key, value, width, bullet=True):
    """'. Key: ........ value', padded with dots to exactly `width` characters."""
    head = ([(". ", "dots")] if bullet else []) + key_segments(key) + [(":", None)]
    dots = width - length(head) - length(value) - 2
    if dots < 1:
        text = "".join(t for t, _ in head + value)
        raise ValueError(f"Line too long by {1 - dots} characters, shorten it: {text}")
    return head + [(" " + "." * dots + " ", "dots")] + value


def rule(title, width):
    return [(title, None), (" " + "—" * (width - len(title) - 1), None)]


def number(n):
    return f"{n:,}"


def info_lines(config, stats, age):
    lines = [rule(config["title"], WIDTH)]
    for index, section in enumerate(config["sections"]):
        if index:
            lines.append([])
            lines.append(rule("- " + section["title"], WIDTH))
        for row in section["rows"]:
            if row is None:
                lines.append([(". ", "dots")])
                continue
            key, value = row
            lines.append(dotted(key, [(value.replace("{uptime}", age), "value")], WIDTH))

    s = stats["totals"]
    lines.append([])
    lines.append(rule("- GitHub Stats", WIDTH))
    right = WIDTH - STATS_LEFT - 3
    repos_value = [(number(s["repos"]), "value"), (" {", None), ("Contributed", "key"), (": ", None),
                   (number(s["contributed"]), "value"), ("}", None)]
    lines.append(dotted("Repos", repos_value, STATS_LEFT) + [(" | ", None)]
                 + dotted("Commits", [(number(s["commits"]), "value")], right, bullet=False))
    loc_value = [(number(s["loc"]), "value"), (" ( ", None), (number(s["add"]) + "++", "add"),
                 (", ", None), (number(s["del"]) + "--", "del"), (" )", None)]
    lines.append(dotted("Lines of Code", loc_value, WIDTH))
    return lines


def render(theme, art, lines):
    colors = THEMES[theme]
    height = TOP + max((len(lines) - 1) * LINE_HEIGHT, (len(art) - 1) * ART_LINE_HEIGHT) + 20
    out = [
        "<?xml version='1.0' encoding='UTF-8'?>",
        f'<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" font-family="ConsolasFallback,Consolas,Menlo,monospace" '
        f'width="{CARD_WIDTH}px" height="{height}px" font-size="16px">',
        "<style>",
        "@font-face { src: local('Consolas'), local('Consolas Bold'); font-family: 'ConsolasFallback';"
        " font-display: swap; size-adjust: 109%; }",
        *(f".{name} {{fill: {colors[name]};}}" for name in ("key", "value", "add", "del", "dots")),
        "text, tspan {white-space: pre;}",
        "</style>",
        f'<rect width="{CARD_WIDTH}px" height="{height}px" fill="{colors["background"]}" rx="15"/>',
    ]
    for i, row in enumerate(art):
        out.append(f'<text x="{ART_X}" y="{TOP - 4 + i * ART_LINE_HEIGHT}" font-size="{ART_FONT_SIZE}px" '
                   f'fill="{colors["text"]}">{escape(row)}</text>')
    for i, segments in enumerate(lines):
        body = "".join(
            f'<tspan class="{css}">{escape(text)}</tspan>' if css else escape(text) for text, css in segments
        )
        out.append(f'<text x="{INFO_X}" y="{TOP + i * LINE_HEIGHT}" fill="{colors["text"]}">{body}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    art = (ROOT / "ascii.txt").read_text(encoding="utf-8").rstrip("\n").split("\n")
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {"totals": {
        k: 0 for k in ("repos", "contributed", "commits", "loc", "add", "del")}}

    token = os.environ.get("ACCESS_TOKEN")
    stats = fetch_stats(token, config["login"], cache) if token else cache
    if token:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(stats, indent=1, sort_keys=True) + "\n")

    birthdate = os.environ.get("BIRTHDATE")
    age = uptime(birthdate, datetime.date.today()) if birthdate else "set BIRTHDATE"
    lines = info_lines(config, stats, age)
    for theme in THEMES:
        (ROOT / f"{theme}_mode.svg").write_text(render(theme, art, lines), encoding="utf-8")


if __name__ == "__main__":
    main()
