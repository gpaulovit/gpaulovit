#!/usr/bin/env python3
"""
Gera dois cartoes SVG estaticos (stats.svg e top-langs.svg) a partir dos dados
publicos/privados do GitHub do usuario, no tema roxo do perfil.

Variaveis de ambiente esperadas:
  GH_USERNAME  -> login do GitHub (ex: gpaulovit)
  GH_PAT       -> Personal Access Token com escopo 'repo' e 'read:user'

Uso:
  GH_USERNAME=gpaulovit GH_PAT=xxxx python3 generate_stats.py
"""

import os
import sys
import datetime
import requests

BG = "#12121a"
BORDER = "#2E1065"
TITLE = "#A78BFA"
TEXT = "#E5E7EB"
MUTED = "#6B7280"
BAR_SHADES = ["#A78BFA", "#8B5CF6", "#7C3AED", "#6D28D9", "#5B21B6", "#4C1D95"]

GRAPHQL_URL = "https://api.github.com/graphql"


def gh_graphql(query: str, variables: dict, token: str) -> dict:
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers={"Authorization": f"bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def fetch_profile(username: str, token: str) -> dict:
    query = """
    query($login: String!) {
      user(login: $login) {
        createdAt
        followers { totalCount }
        repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
          totalCount
          nodes {
            stargazerCount
            forkCount
            languages(first: 5, orderBy: {field: SIZE, direction: DESC}) {
              edges { size node { name } }
            }
          }
        }
      }
    }
    """
    data = gh_graphql(query, {"login": username}, token)["user"]

    created = datetime.datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)

    total_commits = 0
    total_prs = 0
    total_issues = 0
    total_reviews = 0

    year_query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          totalPullRequestContributions
          totalIssueContributions
          totalPullRequestReviewContributions
        }
      }
    }
    """
    year = created.year
    while year <= now.year:
        frm = max(created, datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc))
        to = min(now, datetime.datetime(year, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc))
        yr_data = gh_graphql(
            year_query,
            {"login": username, "from": frm.isoformat(), "to": to.isoformat()},
            token,
        )["user"]["contributionsCollection"]
        total_commits += yr_data["totalCommitContributions"]
        total_prs += yr_data["totalPullRequestContributions"]
        total_issues += yr_data["totalIssueContributions"]
        total_reviews += yr_data["totalPullRequestReviewContributions"]
        year += 1

    repos = data["repositories"]["nodes"]
    total_stars = sum(r["stargazerCount"] for r in repos)
    total_forks = sum(r["forkCount"] for r in repos)

    lang_bytes = {}
    for r in repos:
        for edge in r["languages"]["edges"]:
            name = edge["node"]["name"]
            lang_bytes[name] = lang_bytes.get(name, 0) + edge["size"]

    return {
        "followers": data["followers"]["totalCount"],
        "public_repos": data["repositories"]["totalCount"],
        "total_stars": total_stars,
        "total_forks": total_forks,
        "total_commits": total_commits,
        "total_prs": total_prs,
        "total_issues": total_issues,
        "total_reviews": total_reviews,
        "lang_bytes": lang_bytes,
    }


# ---------- Renderizacao SVG ----------

def render_stats_svg(username: str, s: dict) -> str:
    rows = [
        ("Estrelas conquistadas", s["total_stars"]),
        ("Repositórios públicos", s["public_repos"]),
        ("Pull Requests abertos", s["total_prs"]),
        ("Issues abertas", s["total_issues"]),
        ("Revisões de código", s["total_reviews"]),
        ("Seguidores", s["followers"]),
        ("Commits (histórico total)", s["total_commits"]),
    ]

    width, row_h, top_pad = 460, 30, 70
    height = top_pad + row_h * len(rows) + 20

    body = []
    for i, (label, value) in enumerate(rows):
        y = top_pad + i * row_h
        shade = BAR_SHADES[i % len(BAR_SHADES)]
        body.append(f'''
        <g transform="translate(25, {y})">
          <rect x="0" y="4" width="8" height="8" rx="2" fill="{shade}"/>
          <text x="20" y="13" fill="{TEXT}" font-size="13" font-family="Segoe UI, Helvetica, Arial, sans-serif">{label}</text>
          <text x="{width - 45}" y="13" fill="{TITLE}" font-size="14" font-weight="700" font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="end">{value:,}</text>
        </g>''')

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="GitHub stats de {username}">
  <rect x="0.5" y="0.5" rx="12" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>
  <text x="25" y="35" fill="{TITLE}" font-size="17" font-weight="700" font-family="Segoe UI, Helvetica, Arial, sans-serif">Estatísticas do GitHub</text>
  <line x1="25" y1="46" x2="{width - 25}" y2="46" stroke="{BORDER}" stroke-width="1"/>
  {''.join(body)}
</svg>'''
    return svg


def render_langs_svg(username: str, lang_bytes: dict) -> str:
    total = sum(lang_bytes.values()) or 1
    top = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)[:6]

    width, row_h, top_pad = 460, 34, 60
    height = top_pad + row_h * len(top) + 15
    bar_max_w = width - 190

    body = []
    for i, (name, size) in enumerate(top):
        pct = size / total * 100
        y = top_pad + i * row_h
        bar_w = max(4, bar_max_w * (size / total))
        color = BAR_SHADES[i % len(BAR_SHADES)]
        body.append(f'''
        <g transform="translate(25, {y})">
          <text x="0" y="14" fill="{TEXT}" font-size="13" font-family="Segoe UI, Helvetica, Arial, sans-serif">{name}</text>
          <text x="{width - 50}" y="14" fill="{MUTED}" font-size="12" font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="end">{pct:.1f}%</text>
          <rect x="0" y="20" width="{bar_max_w}" height="6" rx="3" fill="{BORDER}" opacity="0.4"/>
          <rect x="0" y="20" width="{bar_w:.1f}" height="6" rx="3" fill="{color}"/>
        </g>''')

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Linguagens mais usadas por {username}">
  <rect x="0.5" y="0.5" rx="12" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>
  <text x="25" y="30" fill="{TITLE}" font-size="17" font-weight="700" font-family="Segoe UI, Helvetica, Arial, sans-serif">Linguagens mais usadas</text>
  <line x1="25" y1="41" x2="{width - 25}" y2="41" stroke="{BORDER}" stroke-width="1"/>
  {''.join(body)}
</svg>'''
    return svg


def main():
    username = os.environ.get("GH_USERNAME")
    token = os.environ.get("GH_PAT")
    if not username or not token:
        print("Defina GH_USERNAME e GH_PAT como variaveis de ambiente.", file=sys.stderr)
        sys.exit(1)

    stats = fetch_profile(username, token)

    os.makedirs("dist", exist_ok=True)
    with open("dist/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_svg(username, stats))
    with open("dist/top-langs.svg", "w", encoding="utf-8") as f:
        f.write(render_langs_svg(username, stats["lang_bytes"]))

    print("Gerado: dist/stats.svg e dist/top-langs.svg")


if __name__ == "__main__":
    main()
