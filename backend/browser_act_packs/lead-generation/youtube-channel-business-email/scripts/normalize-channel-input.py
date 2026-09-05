import argparse
import json
import re
import sys


def normalize(raw: str) -> dict:
    s = raw.strip()
    if not s:
        return {"error": True, "message": "empty input"}

    full_url_match = re.match(r"^https?://", s, re.IGNORECASE)
    handle_match = re.match(r"^@([A-Za-z0-9._\-]{1,100})$", s)
    channel_id_match = re.match(r"^(UC[0-9A-Za-z_\-]{22})$", s)

    if channel_id_match:
        cid = channel_id_match.group(1)
        return {"about_url": f"https://www.youtube.com/channel/{cid}/about",
                "input_type": "channel_id", "value": cid}

    if handle_match:
        handle = handle_match.group(1)
        return {"about_url": f"https://www.youtube.com/@{handle}/about",
                "input_type": "handle", "value": "@" + handle}

    if full_url_match:
        base = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        cid_in_url = re.search(r"/channel/(UC[0-9A-Za-z_\-]{22})", base)
        handle_in_url = re.search(r"/@([A-Za-z0-9._\-]{1,100})", base)
        if cid_in_url:
            cid = cid_in_url.group(1)
            return {"about_url": f"https://www.youtube.com/channel/{cid}/about",
                    "input_type": "channel_url", "value": cid}
        if handle_in_url:
            handle = handle_in_url.group(1)
            return {"about_url": f"https://www.youtube.com/@{handle}/about",
                    "input_type": "channel_url", "value": "@" + handle}
        custom_url_match = re.search(r"youtube\.com/(c|user)/([^/]+)", base)
        if custom_url_match:
            slug = custom_url_match.group(2)
            about_url = (
                f"https://www.youtube.com/{custom_url_match.group(1)}/{slug}/about"
            )
            return {"about_url": about_url,
                    "input_type": "legacy_custom_url", "value": slug}
        if base.endswith("/about"):
            return {"about_url": base, "input_type": "raw_url", "value": base}
        return {"about_url": base + "/about", "input_type": "raw_url", "value": base}

    bare_handle = re.match(r"^([A-Za-z0-9._\-]{1,100})$", s)
    if bare_handle:
        handle = bare_handle.group(1)
        return {"about_url": f"https://www.youtube.com/@{handle}/about",
                "input_type": "bare_handle", "value": "@" + handle}

    return {"error": True, "message": f"unrecognized input: {raw}"}


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(
        description=(
            "Normalize one channel input (handle, channel id, or full URL) "
            "into a canonical /about URL"
        )
    )
    parser.add_argument("channel", help="Channel id (UCxxx), handle (@name), or URL")
    args = parser.parse_args()
    print(json.dumps(normalize(args.channel), ensure_ascii=False))


if __name__ == "__main__":
    main()
