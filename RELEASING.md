# Releasing

Aggrete is published to PyPI as [`aggrete`](https://pypi.org/project/aggrete/)
and as a container image on GHCR. Releases are cut from `main`.

Publishing is automated: creating a **GitHub Release** builds the image and
publishes the package. PyPI authentication uses **Trusted Publishing** (OIDC),
so no API token is stored in this repository.

## One time setup

Both steps are done once, in a browser, by a maintainer.

1. **PyPI Trusted Publisher.** On
   <https://pypi.org/manage/project/aggrete/settings/publishing/>, add a GitHub
   publisher:
   - Owner: `cjohannsen81`
   - Repository: `aggrete`
   - Workflow name: `release.yml`
   - Environment name: `pypi`

2. **GitHub environment.** In the repository, Settings, Environments, create an
   environment named `pypi`. Optionally add required reviewers so a release
   pauses for a manual approval before it reaches PyPI.

No secrets are needed. The workflow requests a short-lived OIDC token at publish
time; PyPI trusts it because of the publisher configured above.

## Cut a release

1. Bump `version` in `pyproject.toml`. Use semantic versioning. A version on PyPI
   is immutable, so every change must ship under a new number.
2. Update the changelog or release notes. Commit and push to `main`.
3. On GitHub, Releases, "Draft a new release". Create a new tag `v<version>`
   (for example `v0.1.1`) targeting `main`, write the notes, and Publish.
4. The `release` workflow runs automatically:
   - builds the sdist and wheel, runs `twine check`, and checks that the tag
     matches the `pyproject.toml` version,
   - publishes to PyPI via Trusted Publishing,
   - builds and pushes `ghcr.io/cjohannsen81/aggrete:<tag>` and `:latest`.
5. Confirm from a clean environment:

   ```bash
   pip install aggrete
   aggrete --help
   ```

If the tag and the `pyproject.toml` version disagree, the PyPI job fails on
purpose before anything is published. Bump one to match and cut a new tag.

## Manual fallback

If you need to publish without the workflow (Trusted Publishing does not work
from a laptop, so this path uses an API token):

```bash
rm -rf dist/ && python -m build
twine check dist/*
twine upload dist/*        # username __token__, password a project-scoped token
```

## Notes

- `dist/` is build output and is not committed.
- Keep PyPI tokens project-scoped, and delete any account-wide token that was
  used for a first manual upload.
