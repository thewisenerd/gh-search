import base64
import json
from dataclasses import dataclass
from pathlib import Path

import click
import subprocess

import httpx
import structlog
from .cache import Cache
from xdg_base_dirs import xdg_cache_home
import httplink

# https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28
# > To satisfy that need, the GitHub REST API provides up to 1,000 results for each search.
# https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-code
# wtf1. > Searches for query terms inside of a file. This method returns up to 100 results per page.
#       what's the point of 1000?
# wtf2. > The Search code endpoint requires you to authenticate
#       > and limits you to 10 requests per minute.
#       > For unauthenticated requests, the rate limit allows you
#       > to make up to 10 requests per minute.
#       what's the point of auth?

logger: structlog.stdlib.BoundLogger = structlog.get_logger()
_default_headers = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}
_default_endpoint = "https://api.github.com/search/code"
_default_max_results = 1000 # basically 1 search per minute, per above rate limits. damn.
_default_cache_path = Path(xdg_cache_home()) / "gh_search"

def get_auth_token(github_token: str | None, user: str | None) -> str | None:
    if github_token is not None and len(github_token) > 0:
        logger.info("using provided GitHub token")
        return github_token

    logger.info("using \"gh\" CLI to get GitHub token")
    args = ["gh", "auth", "token"] + (["--user", user] if user else [])
    return subprocess.check_output(args, text=True).strip()

def search_impl(
    client: httpx.Client,
    query: str,
    per_page: int,
    page: int,
) -> httpx.Response:
    r = client.get(_default_endpoint, params={"q": query, "per_page": per_page, "page": page})
    r.raise_for_status()
    return r

def paginated(
    client: httpx.Client,
    cache: Cache,
    query: str,
    max_results: int,
):
    page = 0
    per_page = min(max_results, 100)

    total = 0
    while total < max_results:
        page += 1
        cache_key = {
            "query": query,
            "per_page": per_page,
            "page": page,
        }
        if (data := cache.get(cache_key)) is None:
            r = search_impl(client, query, per_page, page)
            data = r.text
            cache.put(cache_key, data)

            link_header = r.headers.get("link", None)
            links = httplink.parse_link_header(link_header)
            if "next" not in links:
                logger.debug("no next link, ending pagination", page=page)
                break

        response = json.loads(data)

        # TODO: a simple heuristic to invalidate cache is if total_count is mismatched
        #   but idk what to do with it yet.
        total_count = response.get("total_count", 0)
        if total_count == 0:
            logger.debug("no results, ending pagination", page=page)
            break

        items = response.get("items", [])

        # TODO: handle this better with caching.
        if len(items) == 0:
            logger.debug("no items in response, ending pagination", page=page)
            break

        yield from items

@dataclass(frozen=True)
class FoundItem:
    repo: str
    path: str

@click.command()
@click.option('-u', '--user', help='use a specific GitHub user for invoking "gh auth token" commands')
@click.option('--github-token', envvar=["GITHUB_TOKEN"], help='use a specific GitHub token for authentication')
@click.option("--max-results", type=int, default=_default_max_results, help='maximum number of results to return')
@click.option("--cache-dir", type=click.Path(file_okay=False, dir_okay=True, writable=True, readable=True), help='directory to use for caching results')
@click.option("--cache-ttl", type=int, default=3600, help='duration (in seconds) to cache results (to avoid rate limits)')
@click.argument('query', required=True)
def search(user: str | None, github_token: str | None, query: str, max_results: int, cache_dir: str | None, cache_ttl: int) -> None:
    token = get_auth_token(github_token=github_token, user=user)
    headers = _default_headers | {"Authorization": f"Bearer {token}"}

    query = query.strip()
    if len(query) == 0:
        raise click.UsageError("query must not be empty")

    found_items: set[FoundItem] = set()
    with httpx.Client(headers=headers) as client:
        with Cache(
            Path(cache_dir if cache_dir else _default_cache_path),
            cache_ttl,
        ) as cache:
            for item in paginated(client, cache, query, max_results):
                path = item.get("path", None)
                if not path:
                    raise click.ClickException("item missing path")

                repo = item.get("repository", {}).get("full_name", None)
                if not repo:
                    raise click.ClickException("item missing repository.full_name")

                logger.debug("item", repo=repo, path=path)

                found_item = FoundItem(repo=repo, path=path)
                if found_item in found_items:
                    logger.debug("duplicate item, skipping", repo=repo, path=path)
                    continue
                found_items.add(found_item)

                git_url = item.get("git_url", None)
                if not git_url:
                    raise click.ClickException("item missing git_url")

                file_path = Path(repo) / path
                file_path.parent.mkdir(parents=True, exist_ok=True)

                if file_path.exists():
                    logger.debug("file exists, skipping", repo=repo, path=path)
                    continue

                logger.debug("downloading", repo=repo, path=path, git_url=git_url)
                r = client.get(git_url)
                r.raise_for_status()

                data = r.json()
                content = data.get("content", None)
                if content is None:
                    raise click.ClickException("item missing content")

                encoding = data.get("encoding", None)
                if encoding == "base64":
                    content = base64.b64decode(content)

                file_path.write_bytes(content)

if __name__ == '__main__':
    search()
