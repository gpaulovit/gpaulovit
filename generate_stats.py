
import os
import sys
import datetime
import requests

# ---------- Paleta (mesma do README) ----------
BG = "#12121a"
BORDER = "#2E1065"
TITLE = "#A78BFA"
TEXT = "#E5E7EB"
MUTED = "#6B7280"
BAR_SHADES = ["#A78BFA", "#8B5CF6", "#7C3AED", "#6D28D9", "#5B21B6", "#4C1D95"]

GRAPHQL_URL = "https://api.github.com/graphql"

# Limiares para os selos de "Conquistas" (trophies) — escala própria, não
# relacionada ao cálculo de rank abaixo.
THRESHOLDS = {
    "total_commits": 1000,
    "total_prs": 50,
    "total_reviews": 50,
    "total_issues": 25,
    "total_stars": 50,
    "followers": 50,
}
TIERS = ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]
TIER_CUTOFFS = [0.90, 0.80, 0.65, 0.50, 0.40, 0.30, 0.20, 0.10, 0.0]


def letter_grade(ratio: float):
    ratio = max(0.0, min(1.0, ratio))
    for tier, cutoff in zip(TIERS, TIER_CUTOFFS):
        if ratio >= cutoff:
            idx = TIERS.index(tier)
            color = BAR_SHADES[min(idx, len(BAR_SHADES) - 1)]
            return tier, color
    return "C", BAR_SHADES[-1]


# ---------- Rank oficial (reproduz src/calculateRank.js do github-readme-stats) ----------
# Validado contra o teste do projeto: commits=125, prs=25, issues=10, stars=25,
# followers=5 (all_commits=False) -> level "B-", percentile 69.333868386557

def exponential_cdf(x: float) -> float:
    return 1 - (2 ** (-x))


def log_normal_cdf(x: float) -> float:
    return x / (1 + x)


def calculate_official_rank(s: dict):
    COMMITS_MEDIAN, COMMITS_WEIGHT = 1000, 2  # all_commits=True (histórico total)
    PRS_MEDIAN, PRS_WEIGHT = 50, 3
    ISSUES_MEDIAN, ISSUES_WEIGHT = 25, 1
    STARS_MEDIAN, STARS_WEIGHT = 50, 4
    FOLLOWERS_MEDIAN, FOLLOWERS_WEIGHT = 10, 1
    TOTAL_WEIGHT = COMMITS_WEIGHT + PRS_WEIGHT + ISSUES_WEIGHT + STARS_WEIGHT + FOLLOWERS_WEIGHT

    raw = (
        COMMITS_WEIGHT * exponential_cdf(s["total_commits"] / COMMITS_MEDIAN)
        + PRS_WEIGHT * exponential_cdf(s["total_prs"] / PRS_MEDIAN)
        + ISSUES_WEIGHT * exponential_cdf(s["total_issues"] / ISSUES_MEDIAN)
        + STARS_WEIGHT * log_normal_cdf(s["total_stars"] / STARS_MEDIAN)
        + FOLLOWERS_WEIGHT * log_normal_cdf(s["followers"] / FOLLOWERS_MEDIAN)
    ) / TOTAL_WEIGHT

    percentile = 100 * (1 - raw)
    thresholds = [1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100]
    levels = ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]
    level = next(lvl for lvl, t in zip(levels, thresholds) if percentile <= t)
    return level, percentile, raw


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

    # soma commits/PRs/issues ano a ano, desde a criacao da conta,
    # pois contributionsCollection so cobre 1 ano por chamada
    created = datetime.datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)

    total_commits = 0
    total_prs = 0
    total_issues = 0
    total_reviews = 0
    daily_contributions = []  # lista de (data_iso, contagem), em ordem cronológica

    year_query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          totalPullRequestContributions
          totalIssueContributions
          totalPullRequestReviewContributions
          contributionCalendar {
            weeks {
              contributionDays { date contributionCount }
            }
          }
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
        for week in yr_data["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                daily_contributions.append((day["date"], day["contributionCount"]))
        year += 1

    daily_contributions.sort(key=lambda d: d[0])

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
        "daily_contributions": daily_contributions,
    }


def fetch_calendar(username: str, token: str) -> list:
    query = """
    query($login: String!) {
      user(login: $login) {
        contributionsCollection {
          contributionCalendar {
            weeks {
              contributionDays { date contributionCount }
            }
          }
        }
      }
    }
    """
    data = gh_graphql(query, {"login": username}, token)["user"]
    weeks = data["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [sum(d["contributionCount"] for d in w["contributionDays"]) for w in weeks]


def render_activity_svg(username: str, weekly_totals: list) -> str:
    width, height = 460, 190
    pad_l, pad_r, pad_t, pad_b = 25, 20, 45, 25
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    max_val = max(weekly_totals) or 1
    n = max(1, len(weekly_totals))
    step = plot_w / max(1, n - 1)

    points = []
    for i, v in enumerate(weekly_totals):
        x = pad_l + i * step
        y = pad_t + plot_h - (v / max_val) * plot_h
        points.append((x, y))

    line_path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_path = (
        line_path
        + f" L {points[-1][0]:.1f},{pad_t + plot_h:.1f}"
        + f" L {points[0][0]:.1f},{pad_t + plot_h:.1f} Z"
    )
    total = sum(weekly_totals)

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Atividade de commits de {username} nas ultimas 52 semanas">
  <rect x="0.5" y="0.5" rx="12" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>
  <text x="25" y="30" fill="{TITLE}" font-size="17" font-weight="700" font-family="Segoe UI, Helvetica, Arial, sans-serif">Atividade recente</text>
  <text x="{width - 25}" y="30" fill="{MUTED}" font-size="12" font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="end">{total:,} contribuições</text>
  <line x1="25" y1="41" x2="{width - 25}" y2="41" stroke="{BORDER}" stroke-width="1"/>
  <path d="{area_path}" fill="{TITLE}" opacity="0.18"/>
  <path d="{line_path}" fill="none" stroke="{TITLE}" stroke-width="2"/>
</svg>'''
    return svg


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

    rank_zone = 130
    left_w = 400
    width = left_w + rank_zone
    row_h, top_pad = 30, 70
    height = top_pad + row_h * len(rows) + 20

    body = []
    for i, (label, value) in enumerate(rows):
        y = top_pad + i * row_h
        shade = BAR_SHADES[i % len(BAR_SHADES)]
        body.append(f'''
        <g transform="translate(25, {y})">
          <rect x="0" y="4" width="8" height="8" rx="2" fill="{shade}"/>
          <text x="20" y="13" fill="{TEXT}" font-size="13" font-family="Segoe UI, Helvetica, Arial, sans-serif">{label}</text>
          <text x="{left_w - 25}" y="13" fill="{TITLE}" font-size="14" font-weight="700" font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="end">{value:,}</text>
        </g>''')

    tier, percentile, raw = calculate_official_rank(s)
    levels_order = ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]
    tier_color = BAR_SHADES[min(levels_order.index(tier), len(BAR_SHADES) - 1)]
    cx = left_w + rank_zone / 2
    cy = height / 2
    r = 42
    circumference = 2 * 3.14159265 * r
    dash = circumference * raw

    rank_group = f'''
    <g>
      <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{BORDER}" stroke-width="8"/>
      <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{tier_color}" stroke-width="8"
              stroke-linecap="round" stroke-dasharray="{dash:.1f} {circumference:.1f}"
              transform="rotate(-90 {cx} {cy})"/>
      <text x="{cx}" y="{cy + 10}" fill="{tier_color}" font-size="30" font-weight="800"
            font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">{tier}</text>
      <text x="{cx}" y="{cy + r + 22}" fill="{MUTED}" font-size="11"
            font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">Classificação</text>
      <text x="{cx}" y="{cy + r + 37}" fill="{MUTED}" font-size="10"
            font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">top {percentile:.1f}%</text>
    </g>'''

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="GitHub stats de {username}">
  <rect x="0.5" y="0.5" rx="12" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>
  <text x="25" y="35" fill="{TITLE}" font-size="17" font-weight="700" font-family="Segoe UI, Helvetica, Arial, sans-serif">Estatísticas do GitHub</text>
  <line x1="25" y1="46" x2="{width - 25}" y2="46" stroke="{BORDER}" stroke-width="1"/>
  <line x1="{left_w}" y1="55" x2="{left_w}" y2="{height - 15}" stroke="{BORDER}" stroke-width="1" opacity="0.5"/>
  {''.join(body)}
  {rank_group}
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


def render_trophies_svg(username: str, s: dict) -> str:
    categories = [
        ("Estrelas", "total_stars"),
        ("Commits", "total_commits"),
        ("Pull Reqs", "total_prs"),
        ("Issues", "total_issues"),
        ("Revisões", "total_reviews"),
        ("Seguidores", "followers"),
    ]

    box_w, box_h, gap, top_pad = 72, 90, 8, 55
    width = len(categories) * box_w + (len(categories) - 1) * gap + 50
    height = top_pad + box_h + 20

    body = []
    for i, (label, key) in enumerate(categories):
        ratio = min(1.0, s[key] / THRESHOLDS[key])
        tier, color = letter_grade(ratio)
        x = 25 + i * (box_w + gap)
        y = top_pad
        body.append(f'''
        <g transform="translate({x}, {y})">
          <rect x="0" y="0" width="{box_w}" height="{box_h}" rx="10" fill="none" stroke="{color}" stroke-width="1.5" opacity="0.7"/>
          <text x="{box_w/2}" y="34" fill="{color}" font-size="22" font-weight="800" font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">{tier}</text>
          <text x="{box_w/2}" y="56" fill="{TEXT}" font-size="11" font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">{label}</text>
          <text x="{box_w/2}" y="74" fill="{MUTED}" font-size="10" font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">{s[key]:,}</text>
        </g>''')

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Conquistas de {username}">
  <rect x="0.5" y="0.5" rx="12" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>
  <text x="25" y="30" fill="{TITLE}" font-size="17" font-weight="700" font-family="Segoe UI, Helvetica, Arial, sans-serif">Conquistas</text>
  {''.join(body)}
</svg>'''
    return svg


MONTHS_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def fmt_date(date_iso: str) -> str:
    y, m, d = date_iso.split("-")
    return f"{int(d)} {MONTHS_PT[int(m) - 1]} {y}"


def compute_streaks(daily: list) -> dict:
    if not daily:
        return None

    total = sum(c for _, c in daily)

    # streak atual: conta de trás pra frente; se o ultimo dia (hoje) ainda
    # nao tem contribuicao, pula ele (o dia ainda nao acabou) sem quebrar a sequencia
    idx = len(daily) - 1
    if daily[idx][1] == 0:
        idx -= 1
    current_streak = 0
    current_start = current_end = None
    while idx >= 0 and daily[idx][1] > 0:
        if current_streak == 0:
            current_end = daily[idx][0]
        current_start = daily[idx][0]
        current_streak += 1
        idx -= 1

    # maior streak: maior sequencia consecutiva de dias com contribuicao > 0
    longest = 0
    longest_start = longest_end = None
    run = 0
    run_start = None
    for date_iso, count in daily:
        if count > 0:
            if run == 0:
                run_start = date_iso
            run += 1
            if run > longest:
                longest = run
                longest_start = run_start
                longest_end = date_iso
        else:
            run = 0

    return {
        "total": total,
        "range_start": daily[0][0],
        "range_end": daily[-1][0],
        "current_streak": current_streak,
        "current_start": current_start,
        "current_end": current_end,
        "longest_streak": longest,
        "longest_start": longest_start,
        "longest_end": longest_end,
    }


def render_streak_svg(username: str, sk: dict) -> str:
    width, height = 460, 150
    col_w = width / 3

    def col(cx, big, label, sub):
        return f'''
        <g>
          <text x="{cx}" y="70" fill="{TITLE}" font-size="32" font-weight="800"
                font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">{big}</text>
          <text x="{cx}" y="94" fill="{TEXT}" font-size="12"
                font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">{label}</text>
          <text x="{cx}" y="112" fill="{MUTED}" font-size="10"
                font-family="Segoe UI, Helvetica, Arial, sans-serif" text-anchor="middle">{sub}</text>
        </g>'''

    total_sub = f"{fmt_date(sk['range_start'])} — {fmt_date(sk['range_end'])}"
    if sk["current_streak"] > 0:
        current_sub = f"{fmt_date(sk['current_start'])} — {fmt_date(sk['current_end'])}"
    else:
        current_sub = "Sem sequência ativa"
    longest_sub = (
        f"{fmt_date(sk['longest_start'])} — {fmt_date(sk['longest_end'])}"
        if sk["longest_streak"] > 0 else "—"
    )

    r = 46
    cx2 = width / 2

    body = col(col_w / 2, f"{sk['total']:,}", "Total de contribuições", total_sub)
    body += f'<circle cx="{cx2}" cy="55" r="{r}" fill="none" stroke="{BORDER}" stroke-width="2"/>'
    body += col(cx2, sk["current_streak"], "Sequência atual", current_sub)
    body += col(width - col_w / 2, sk["longest_streak"], "Maior sequência", longest_sub)

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Sequência de contribuições de {username}">
  <rect x="0.5" y="0.5" rx="12" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}" stroke-width="1"/>
  <line x1="{col_w}" y1="20" x2="{col_w}" y2="{height - 20}" stroke="{BORDER}" stroke-width="1" opacity="0.5"/>
  <line x1="{2 * col_w}" y1="20" x2="{2 * col_w}" y2="{height - 20}" stroke="{BORDER}" stroke-width="1" opacity="0.5"/>
  {body}
</svg>'''
    return svg


def main():
    username = os.environ.get("GH_USERNAME")
    token = os.environ.get("GH_PAT")
    if not username or not token:
        print("Defina GH_USERNAME e GH_PAT como variaveis de ambiente.", file=sys.stderr)
        sys.exit(1)

    stats = fetch_profile(username, token)
    weekly_totals = fetch_calendar(username, token)

    os.makedirs("dist", exist_ok=True)
    with open("dist/stats.svg", "w", encoding="utf-8") as f:
        f.write(render_stats_svg(username, stats))
    with open("dist/top-langs.svg", "w", encoding="utf-8") as f:
        f.write(render_langs_svg(username, stats["lang_bytes"]))
    with open("dist/activity.svg", "w", encoding="utf-8") as f:
        f.write(render_activity_svg(username, weekly_totals))
    with open("dist/trophies.svg", "w", encoding="utf-8") as f:
        f.write(render_trophies_svg(username, stats))

    streak_data = compute_streaks(stats["daily_contributions"])
    with open("dist/streak.svg", "w", encoding="utf-8") as f:
        f.write(render_streak_svg(username, streak_data))

    print("Gerado: dist/stats.svg, dist/top-langs.svg, dist/activity.svg, dist/trophies.svg e dist/streak.svg")


if __name__ == "__main__":
    main()
