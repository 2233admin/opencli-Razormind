import argparse
import json
import re
import sys
from urllib.parse import urlparse

SOCIAL_DOMAINS = {
    "youtube.com", "youtu.be",
    "twitter.com", "x.com",
    "facebook.com", "fb.me", "fb.com",
    "instagram.com",
    "tiktok.com",
    "linkedin.com", "lnkd.in",
    "reddit.com",
    "discord.gg", "discord.com",
    "twitch.tv",
    "patreon.com",
    "snapchat.com",
    "threads.net",
    "pinterest.com",
    "telegram.me", "t.me",
    "whatsapp.com",
    "github.com",
    "spotify.com", "open.spotify.com",
    "soundcloud.com",
    "apple.com", "music.apple.com",
    "amazon.com", "amzn.to", "a.co",
    "ebay.com",
    "shopify.com",
}

LINK_AGGREGATOR_DOMAINS = {
    "linktr.ee", "linktree.com",
    "beacons.ai",
    "bio.link",
    "allmylinks.com",
    "lnk.bio",
    "carrd.co",
    "hoo.be",
    "withkoji.com",
    "campsite.bio",
    "snipfeed.co",
    "milkshake.app",
    "tap.bio",
    "mez.ink",
    "stan.store",
    "later.com/linkinbio",
    "msha.ke",
    "shor.by",
    "linkpop.com",
    "shorby.com",
    "elink.io",
}

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-/=])[A-Za-z0-9._%+\-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+"
)
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*[\(\[\{]\s*(?:at|@)\s*[\)\]\}]\s*"
    r"([A-Za-z0-9.\-]+)\s*[\(\[\{]\s*(?:dot|\.)\s*[\)\]\}]\s*"
    r"([A-Za-z]{2,})",
    re.IGNORECASE,
)


def classify_url(url: str) -> dict:
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if not host:
            return {"kind": "invalid", "domain": None, "url": url}
        if host in SOCIAL_DOMAINS or any(host.endswith("." + d) for d in SOCIAL_DOMAINS):
            return {"kind": "social", "domain": host, "url": url}
        if host in LINK_AGGREGATOR_DOMAINS or any(
            host.endswith("." + d) for d in LINK_AGGREGATOR_DOMAINS
        ):
            return {"kind": "link_aggregator", "domain": host, "url": url}
        return {"kind": "personal_or_business", "domain": host, "url": url}
    except Exception as e:
        return {"kind": "invalid", "domain": None, "url": url, "error": str(e)}


def extract_emails(text: str) -> list:
    if not text:
        return []
    matches = EMAIL_RE.findall(text)
    obfuscated = OBFUSCATED_RE.findall(text)
    for parts in obfuscated:
        matches.append(f"{parts[0]}@{parts[1]}.{parts[2]}")
    cleaned = []
    seen = set()
    for raw in matches:
        addr = raw.strip().rstrip(".,;:!?")
        if not addr or "@" not in addr:
            continue
        local, _, domain = addr.partition("@")
        if not local or not domain or "." not in domain:
            continue
        if domain.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")):
            continue
        if domain.lower().endswith((".com.png", ".com.jpg")):
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(addr)
    return cleaned


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description="Extract email addresses from a text blob (markdown / html / plain). "
                    "Optionally classify a URL as social / link aggregator / personal site."
    )
    parser.add_argument("--text", help="Text to scan for emails")
    parser.add_argument("--text-file", help="Path to UTF-8 file to scan for emails")
    parser.add_argument(
        "--classify-url",
        help="Classify a single URL (social/link_aggregator/personal_or_business)",
    )
    args = parser.parse_args()

    output = {}
    if args.classify_url:
        output["classification"] = classify_url(args.classify_url)
    if args.text:
        output["emails"] = extract_emails(args.text)
    elif args.text_file:
        with open(args.text_file, encoding="utf-8", errors="ignore") as fh:
            output["emails"] = extract_emails(fh.read())
    if not output:
        output = {"error": True, "message": "provide --text, --text-file, or --classify-url"}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
