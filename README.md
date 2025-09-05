gh-search
=========

download files off github based on your search query.

# usage

```
gh-search "{query}"
```

this will download the files matching query, to `{owner}/{repo}/{path}`
in the current directory.

# TODO

- [ ] cleanup debug logs, reduce verbosity, to stderr
- [ ] actually handle rate limiting
- [ ] progressbar based on total_count
- [ ] cache invalidation
- [ ] output file invalidation (`git_url` updated?)
