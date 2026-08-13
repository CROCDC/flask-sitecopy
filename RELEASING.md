# Releasing

Releases are driven by the **version in `pyproject.toml`** and shipped by merging to
`main` — no tags to push by hand.

## Branches

- **`develop`** — the integration branch. Feature branches open PRs into `develop`.
- **`main`** — always releasable. A push to it runs the release workflow.

## How a release happens

1. Land your work on `develop` (via PRs). CI runs on every PR and branch.
2. When you want to cut a release, open a PR from `develop` → `main` that **bumps the
   version** in `pyproject.toml` and adds a `## [x.y.z]` section to
   [`CHANGELOG.md`](CHANGELOG.md).
3. Merge it. The [`Release` workflow](.github/workflows/release.yml) runs on the push to
   `main` and:
   - runs the test suite (with the coverage gate);
   - checks whether `vX.Y.Z` is already tagged;
   - if **not**, builds the sdist + wheel, publishes to **PyPI via trusted publishing**,
     then creates the `vX.Y.Z` **git tag** and a **GitHub Release** whose notes are that
     version's changelog section.

A merge that does **not** change the version finds its tag already there and releases
nothing, so ordinary merges to `main` are safe. Publishing is idempotent
(`skip-existing`), so a re-run never double-publishes.

**So the whole release is: bump the version, merge to `main`.** That is a step that can be
done entirely through GitHub (no local tag push), which is the point.

## One-time setup: PyPI trusted publisher

Trusted publishing needs a publisher configured once on PyPI (no API token is stored in
the repo). On <https://pypi.org> → the project → *Publishing*, add a GitHub publisher:

| field | value |
|-------|-------|
| Owner | `CROCDC` |
| Repository | `flask-sitecopy` |
| Workflow name | `release.yml` |
| Environment | *(leave blank)* |

If the publish step fails with an OIDC/trusted-publishing error, this is what to check.
No tag is created when publishing fails, so the release simply retries on the next push
(or via *Run workflow* on the Actions tab) once the publisher is set.

## Versioning

[Semantic versioning](https://semver.org/): patch for fixes, minor for
backwards-compatible features, major for breaking changes. `pyproject.toml` is the single
source of truth — `sitecopy.__version__` reads it, and the tag is derived from it.
