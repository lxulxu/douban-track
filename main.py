from __future__ import annotations

import json, os, re, sys
from collections import defaultdict
from datetime import datetime, timedelta

import feedparser, yaml

PREFIX_MAP = {
    "看过": "done", "读过": "done", "听过": "done", "玩过": "done",
    "在看": "doing", "在读": "doing", "在听": "doing",
    "想看": "wish", "想读": "wish", "想听": "wish",
    "最近在看": "doing", "最近在读": "doing", "最近在听": "doing",
}

RATING_MAP = {"力荐": 5, "推荐": 4, "还行": 3, "较差": 2, "很差": 1}

VALID_URL_RE = re.compile(
    r"https?://("
    r"movie\.douban\.com/subject/\d+"
    r"|music\.douban\.com/subject/\d+"
    r"|www\.douban\.com/location/drama/\d+"
    r"|book\.douban\.com/subject/\d+"
    r"|www\.douban\.com/game/\d+"
    r")/$"
)

_URL_TYPE_MAP = [
    ("book.douban.com",             "book"),
    ("movie.douban.com",            "movie"),
    ("music.douban.com",            "music"),
    ("www.douban.com/location/drama", "drama"),
    ("www.douban.com/game",         "game"),
]

DEFAULT_CONFIG = {
    "title": "Y{year}Q{quarter} 影视音总结",
    "date_format": "%Y-%m-%d",
    "categories": ["生活"],
    "tags": ["生活"],
    "sections": {
        "movie": {"icon": "🎬", "label": "电影"},
        "book":  {"icon": "📚", "label": "读书"},
        "music": {"icon": "🎵", "label": "音乐"},
        "game":  {"icon": "🎮", "label": "游戏"},
        "drama": {"icon": "🎭", "label": "戏剧"},
    },
    "file_template": (
        "---\n"
        'title: "{title}"\n'
        "date: {date}\n"
        "categories: [{categories}]\n"
        "tags: [{tags}]\n"
        "---\n\n"
        "{sections}\n"
    ),
    "section_template": "## {icon}{label}\n\n{items}\n",
    "item_template": "- {published}. [**{title}**]({url}) — {rating_stars}",
}


def _fmt(template: str, **kwargs) -> str:
    while True:
        try:
            return template.format(**kwargs)
        except KeyError as e:
            kwargs[e.args[0]] = "{" + e.args[0] + "}"


def parse_feed(rss_url: str) -> dict[str, dict]:
    feed = feedparser.parse(rss_url)
    entries: dict[str, dict] = {}
    for entry in feed.entries:
        if VALID_URL_RE.match(entry.link) and (data := _extract_entry(entry)):
            entries[entry.link] = data
    return entries


def _extract_entry(entry) -> dict | None:
    title, status = _extract_title(entry.title)
    if not title:
        return None
    return {
        "type":      _classify_url(entry.link),
        "title":     title,
        "published": str(datetime.strptime(entry.published,
                         "%a, %d %b %Y %H:%M:%S %Z").date()),
        "image_url": _extract_image_url(entry.description),
        "rating":    _extract_rating(entry.description),
        "status":    status,
    }


def _extract_title(raw_title: str) -> tuple[str | None, str | None]:
    for prefix, status in PREFIX_MAP.items():
        if raw_title.startswith(prefix):
            return raw_title[len(prefix):].strip(), status
    return None, None


def _extract_image_url(description: str) -> str | None:
    if m := re.search(r'src="([^"]+)"', description):
        return m.group(1)
    return None


def _extract_rating(description: str) -> int:
    if (idx := description.find("推荐:")) == -1:
        return 0
    idx += 3
    end = description.find("</p>", idx)
    return RATING_MAP.get(description[idx:end].strip(), 0)


def _classify_url(url: str) -> str:
    for key, typ in _URL_TYPE_MAP:
        if key in url:
            return typ
    return "unknown"


def load_cache(path: str) -> dict[str, dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_cache(path: str, data: dict[str, dict]) -> None:
    existing = load_cache(path)
    existing.update(data)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def _month_end(year: int, month: int):
    if month == 12:
        return datetime(year, 12, 31)
    return datetime(year, month + 1, 1) - timedelta(days=1)


def get_period(period_type: str) -> dict:
    now = datetime.now()
    y = now.year

    if period_type == "quarterly":
        q = (now.month - 1) // 3 + 1
        start = datetime(y, (q - 1) * 3 + 1, 1)
        end = datetime(y, 12, 31) if q == 4 else datetime(y, q * 3 + 1, 1) - timedelta(days=1)
        label = f"Y{y}Q{q}"
        return {"year": y, "quarter": q, "start": start.date(), "end": end.date(),
                "filename": f"{label}-media-summary.md",
                "period_label": label, "period_type": period_type}

    if period_type == "monthly":
        m = now.month
        start = datetime(y, m, 1)
        label = f"Y{y}M{m:02d}"
        return {"year": y, "month": m, "start": start.date(),
                "end": _month_end(y, m).date(),
                "filename": f"{label}-media-summary.md",
                "period_label": label, "period_type": period_type}

    if period_type == "weekly":
        start = now - timedelta(days=now.weekday())
        end = start + timedelta(days=6)
        w = now.isocalendar()[1]
        label = f"Y{y}W{w:02d}"
        return {"year": y, "week": w, "start": start.date(), "end": end.date(),
                "filename": f"{label}-media-summary.md",
                "period_label": label, "period_type": period_type}

    raise ValueError(f"Unknown period_type: {period_type!r}")


def filter_by_date_range(data: dict, start_date, end_date) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = defaultdict(dict)
    for url, item in data.items():
        d = datetime.strptime(item["published"], "%Y-%m-%d").date()
        if start_date <= d <= end_date:
            result[item.get("type", "movie")][url] = item
    for cat in list(result):
        result[cat] = dict(sorted(result[cat].items(),
                                  key=lambda kv: kv[1]["published"]))
    return dict(result)


def _period_fmt(period_info: dict) -> dict[str, str]:
    return {k: str(period_info.get(k, ""))
            for k in ("year", "quarter", "month", "week", "period_type", "period_label")}


def generate_markdown(filtered: dict, config: dict, period_info: dict) -> str:
    pf = _period_fmt(period_info)
    title = _fmt(config["title"], **pf)
    date_str = period_info["start"].strftime(config["date_format"])
    cats_str = ", ".join(config["categories"])
    tags_str = ", ".join(config.get("tags", config["categories"]))
    sec_cfg = config.get("sections", {})

    item_tmpl = config["item_template"]
    section_tmpl = config.get("section_template", DEFAULT_CONFIG["section_template"])
    file_tmpl = config.get("file_template", DEFAULT_CONFIG["file_template"])

    sections: list[str] = []
    for cat_type, items in filtered.items():
        if not items:
            continue
        sc = sec_cfg.get(cat_type, {})
        icon, label = sc.get("icon", ""), sc.get("label", cat_type)

        item_lines = [
            _fmt(item_tmpl, **pf,
                 published=it["published"], title=it["title"], url=url,
                 rating=str(it.get("rating", 0)),
                 rating_stars="★" * it.get("rating", 0) + "☆" * (5 - it.get("rating", 0)),
                 status=it.get("status", "done"), type=it.get("type", ""),
                 image_url=it.get("image_url", ""))
            for url, it in items.items()
        ]
        sections.append(_fmt(section_tmpl, **pf, icon=icon, label=label,
                             items="\n".join(item_lines)))

    return _fmt(file_tmpl, **pf, title=title, date=date_str,
                categories=cats_str, tags=tags_str, sections="\n".join(sections))


def _load_yaml(path: str) -> dict:
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def load_config(user_path: str) -> dict:
    config = dict(DEFAULT_CONFIG)
    user = _load_yaml(user_path)
    if user:
        sections = dict(config["sections"])
        sections.update(user.get("sections", {}))
        config.update(user)
        config["sections"] = sections
    return config


def main() -> None:
    rss_url = os.environ.get("RSS_URL")
    if not rss_url:
        print("Error: RSS_URL environment variable is required.", file=sys.stderr)
        sys.exit(1)

    period_type = os.environ.get("PERIOD", "quarterly")
    if period_type not in ("weekly", "monthly", "quarterly"):
        print(f"Error: PERIOD must be weekly/monthly/quarterly, got {period_type!r}",
              file=sys.stderr)
        sys.exit(1)

    config = load_config("config.yml")
    output_dir = os.environ.get("DOUBAN_OUTPUT", ".")

    new_entries = parse_feed(rss_url)
    save_cache("douban_data.json", new_entries)
    all_data = load_cache("douban_data.json")

    period_info = get_period(period_type)
    filtered = filter_by_date_range(all_data, period_info["start"], period_info["end"])
    if not filtered:
        print(f"No entries for {period_info['period_label']}, skipping.")
        return

    md_content = generate_markdown(filtered, config, period_info)

    output_path = os.path.join(output_dir, period_info["filename"])
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
