"""Notion REST client + block-to-markdown converter for server-side ingest.

Stdlib-only (urllib + json + re) so we don't pull `requests` into the engine
runtime. Notion's API is JSON over HTTPS; this is sufficient.

Public entry points:
- read_token() -> str
- query_database(db_id) -> list[page]
- get_page(page_id) -> page
- get_blocks(block_id) -> list[block]   # recursive children resolved
- page_to_markdown(page, blocks) -> (filename, body)

API version pinned to 2022-06-28 (Notion's stable release).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import paths

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_API_VERSION = "2022-06-28"
HTTP_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
PAGE_SIZE = 100  # Notion's max for paginated endpoints


class NotionError(Exception):
    """Raised on token/auth/network/API failures the user should see."""


# ----------------------------- token I/O ------------------------------------


def read_token() -> str:
    """Read the Notion integration token. Raises NotionError if missing."""
    path = paths.notion_token_path()
    if not path.is_file():
        raise NotionError(
            f"Notion integration token not found at {path}. "
            "Run mcbrain-setup Step 8f to configure, or write the token "
            "manually (single line, no whitespace)."
        )
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise NotionError(f"Notion token file at {path} is empty.")
    return token


# ----------------------------- HTTP -----------------------------------------


def _request(method: str, path_: str, body: dict | None = None) -> dict:
    url = NOTION_API_BASE + path_
    headers = {
        "Authorization": f"Bearer {read_token()}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                payload = resp.read()
                return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            # Retry on rate-limit (429) and 5xx; surface 4xx immediately.
            if e.code == 429 or 500 <= e.code < 600:
                last_err = NotionError(
                    f"Notion API {e.code} on {method} {path_}: {err_body[:200]}"
                )
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
                    continue
                raise last_err from e
            raise NotionError(
                f"Notion API {e.code} on {method} {path_}: {err_body[:500]}"
            ) from e
        except urllib.error.URLError as e:
            last_err = NotionError(f"Notion network error on {method} {path_}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
                continue
            raise last_err
    # Unreachable, but mypy/pyright happy:
    raise last_err or NotionError("unknown Notion API failure")


def _paginate(method: str, path_: str, body: dict | None = None) -> list[dict]:
    """Drain a paginated endpoint. Body is reused across pages with start_cursor."""
    results: list[dict] = []
    cursor: str | None = None
    while True:
        page_body = dict(body or {})
        page_body["page_size"] = PAGE_SIZE
        if cursor:
            page_body["start_cursor"] = cursor
        resp = _request(method, path_, page_body if method != "GET" else None)
        # GET endpoints (e.g. blocks/{id}/children) take query params instead.
        if method == "GET" and (cursor or "page_size" in (body or {})):
            qs = urllib.parse.urlencode(
                {k: v for k, v in page_body.items() if k in ("page_size", "start_cursor")}
            )
            sep = "&" if "?" in path_ else "?"
            resp = _request("GET", f"{path_}{sep}{qs}", None)
        results.extend(resp.get("results", []))
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return results


# ----------------------------- API surface ----------------------------------


def query_database(db_id: str, filter_: dict | None = None) -> list[dict]:
    """Return every page in a database (paginated drain)."""
    body: dict = {}
    if filter_:
        body["filter"] = filter_
    return _paginate("POST", f"/databases/{db_id}/query", body)


def get_page(page_id: str) -> dict:
    """Fetch a single page's properties + metadata (no body content)."""
    return _request("GET", f"/pages/{page_id}")


def get_blocks(block_id: str) -> list[dict]:
    """Recursively fetch all child blocks of `block_id`.

    For each block with `has_children=True`, recursively fetches its children
    and attaches them under the synthetic `_children` key (Notion's API does
    not include children inline; we compose them ourselves).
    """
    children = _paginate("GET", f"/blocks/{block_id}/children")
    for block in children:
        if block.get("has_children"):
            block["_children"] = get_blocks(block["id"])
    return children


# ----------------------------- markdown conversion --------------------------


def _rich_text_to_md(rich: list[dict]) -> str:
    """Convert Notion rich_text array to inline markdown."""
    out: list[str] = []
    for span in rich:
        text = span.get("plain_text", "")
        if not text:
            continue
        ann = span.get("annotations", {})
        if ann.get("code"):
            text = f"`{text}`"
        if ann.get("bold"):
            text = f"**{text}**"
        if ann.get("italic"):
            text = f"*{text}*"
        if ann.get("strikethrough"):
            text = f"~~{text}~~"
        href = span.get("href")
        if href:
            text = f"[{text}]({href})"
        out.append(text)
    return "".join(out)


def _block_to_md(block: dict, list_index: int = 1, depth: int = 0) -> str:
    """Convert a single block (and its `_children`) to markdown.

    `list_index` is used for numbered_list_item. `depth` controls indentation
    for nested lists.
    """
    btype = block.get("type", "")
    indent = "  " * depth
    children = block.get("_children", [])

    if btype == "paragraph":
        text = _rich_text_to_md(block["paragraph"]["rich_text"])
        out = f"{indent}{text}" if text else ""
        if children:
            out += "\n" + _children_to_md(children, depth + 1)
        return out

    if btype.startswith("heading_"):
        level = int(btype.split("_")[1])
        text = _rich_text_to_md(block[btype]["rich_text"])
        return f"{'#' * level} {text}"

    if btype == "bulleted_list_item":
        text = _rich_text_to_md(block["bulleted_list_item"]["rich_text"])
        out = f"{indent}- {text}"
        if children:
            out += "\n" + _children_to_md(children, depth + 1)
        return out

    if btype == "numbered_list_item":
        text = _rich_text_to_md(block["numbered_list_item"]["rich_text"])
        out = f"{indent}{list_index}. {text}"
        if children:
            out += "\n" + _children_to_md(children, depth + 1)
        return out

    if btype == "to_do":
        text = _rich_text_to_md(block["to_do"]["rich_text"])
        checked = "x" if block["to_do"].get("checked") else " "
        return f"{indent}- [{checked}] {text}"

    if btype == "toggle":
        text = _rich_text_to_md(block["toggle"]["rich_text"])
        out = f"{indent}- {text}"
        if children:
            out += "\n" + _children_to_md(children, depth + 1)
        return out

    if btype == "code":
        lang = block["code"].get("language", "")
        text = _rich_text_to_md(block["code"]["rich_text"])
        # Code blocks shouldn't carry inline markdown — strip backticks added
        # by _rich_text_to_md for code annotation.
        text_plain = "".join(s.get("plain_text", "") for s in block["code"]["rich_text"])
        return f"```{lang}\n{text_plain}\n```"

    if btype == "quote":
        text = _rich_text_to_md(block["quote"]["rich_text"])
        # Multi-line quotes get `>` per line.
        return "\n".join(f"> {line}" for line in text.split("\n"))

    if btype == "callout":
        emoji = ""
        icon = block["callout"].get("icon")
        if icon and icon.get("type") == "emoji":
            emoji = icon["emoji"] + " "
        text = _rich_text_to_md(block["callout"]["rich_text"])
        return f"> {emoji}{text}"

    if btype == "divider":
        return "---"

    if btype == "image":
        img = block["image"]
        url = img.get("file", {}).get("url") or img.get("external", {}).get("url", "")
        caption = _rich_text_to_md(img.get("caption", []))
        return f"![{caption}]({url})"

    if btype == "equation":
        expr = block["equation"].get("expression", "")
        return f"$$\n{expr}\n$$"

    if btype == "bookmark":
        url = block["bookmark"].get("url", "")
        caption = _rich_text_to_md(block["bookmark"].get("caption", []))
        return f"[{caption or url}]({url})"

    if btype == "child_page":
        title = block["child_page"].get("title", "Untitled")
        return f"<!-- child_page: {title} (id={block['id']}) -->"

    if btype == "table":
        # Tables have rows as children; each row is a `table_row` block.
        if not children:
            return f"<!-- empty table id={block['id']} -->"
        rows = []
        for row in children:
            cells = row["table_row"].get("cells", [])
            row_md = " | ".join(_rich_text_to_md(c) or "" for c in cells)
            rows.append(f"| {row_md} |")
        if rows:
            # Insert a header separator after row 0.
            sep = "| " + " | ".join("---" for _ in cells) + " |"
            rows.insert(1, sep)
        return "\n".join(rows)

    # Unhandled types — preserve as a comment so the user knows something existed.
    return f"<!-- unsupported notion block type: {btype} -->"


_LIST_BLOCK_TYPES = {"bulleted_list_item", "numbered_list_item", "to_do"}


def _children_to_md(blocks: list[dict], depth: int = 0) -> str:
    """Convert a sequence of sibling blocks to markdown.

    Tracks numbered_list_item runs so consecutive items get sequential indices.
    Joins adjacent list items with single newlines (tight lists); other block
    transitions get blank-line separation.
    """
    parts: list[tuple[str, str]] = []  # (block_type, rendered)
    n_index = 1
    prev_type: str | None = None
    for block in blocks:
        btype = block.get("type", "")
        if btype == "numbered_list_item":
            if prev_type != "numbered_list_item":
                n_index = 1
            rendered = _block_to_md(block, list_index=n_index, depth=depth)
            n_index += 1
        else:
            rendered = _block_to_md(block, depth=depth)
        if rendered:
            parts.append((btype, rendered))
        prev_type = btype

    out: list[str] = []
    for i, (btype, rendered) in enumerate(parts):
        out.append(rendered)
        if i + 1 < len(parts):
            next_type = parts[i + 1][0]
            # Tight join only when both blocks are the same list type (bullets
            # next to bullets, numbered next to numbered, todo next to todo).
            if btype in _LIST_BLOCK_TYPES and btype == next_type:
                out.append("\n")
            else:
                out.append("\n\n")
    return "".join(out)


# ----------------------------- page assembly --------------------------------


_SLUG_NONALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(title: str, fallback: str) -> str:
    s = _SLUG_NONALNUM.sub("-", title.lower()).strip("-")
    return s or fallback


def _page_title(page: dict) -> str:
    """Extract the page's title from its properties (whichever prop is type=title)."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            return _rich_text_to_md(prop["title"]) or "Untitled"
    return "Untitled"


def page_to_markdown(page: dict, blocks: list[dict]) -> tuple[str, str]:
    """Render a Notion page + its blocks as (filename, body).

    Filename: <slugified-title>-<short-id>.md (short-id from page['id'] for
    collision safety).
    Body: YAML frontmatter (notion_id/url/edited/imported) + `# Title` + blocks.
    """
    title = _page_title(page)
    short_id = page["id"].replace("-", "")[:8]
    filename = f"{_slugify(title, short_id)}-{short_id}.md"

    fm_lines = [
        "---",
        f"notion_id: {page['id']}",
        f"notion_url: {page.get('url', '')}",
        f"last_edited_time: {page.get('last_edited_time', '')}",
        f"imported_at: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "---",
    ]
    body = "\n".join(fm_lines) + f"\n\n# {title}\n\n" + _children_to_md(blocks)
    return filename, body.rstrip() + "\n"


# ----------------------------- frontmatter helpers --------------------------


_FM_LINE_RE = re.compile(r"^([a-z_]+):\s*(.*)$")


def read_frontmatter(md_path: Path) -> dict[str, str]:
    """Best-effort YAML frontmatter parser for our own generated files.

    Only supports the flat key:value form we write. Returns empty dict if
    the file has no frontmatter or doesn't exist.
    """
    if not md_path.is_file():
        return {}
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        m = _FM_LINE_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out
