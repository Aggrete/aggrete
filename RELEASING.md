# Releasing

Aggrete is published to PyPI as [`aggrete`](https://pypi.org/project/aggrete/).
Releases are cut from `main`.

## One time setup

- A PyPI account with a verified email and two-factor authentication enabled.
- A project-scoped API token: PyPI, Account settings, API tokens, scope
  "Project: aggrete". Paste it at the upload prompt or store it in `~/.pypirc`
  (`chmod 600`). Never commit it.
- Build tooling in your environment: `pip install build twine`.

## Cut a release

1. Bump `version` in `pyproject.toml`. Use semantic versioning. A version on PyPI
   is immutable, so every change must ship under a new number.
2. Update the changelog or release notes.
3. Build clean artifacts:

   ```bash
   rm -rf dist/ && python -m build
   ```

4. Verify before uploading:

   ```bash
   twine check dist/*
   ```

   Optionally install the wheel into a fresh virtualenv and run `aggrete --help`.

5. Upload:

   ```bash
   twine upload dist/*
   ```

   Username `__token__`, password the API token.

6. Confirm from a clean environment:

   ```bash
   pip install aggrete
   aggrete --help
   ```

7. Tag the release: `git tag v<version> && git push --tags`.

## Notes

- `dist/` is build output and is not committed.
- Once the project exists on PyPI, keep tokens project-scoped and delete any
  account-wide token used for a first upload.
