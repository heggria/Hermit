# Release Rollback

This document covers rollback procedures for all Hermit release channels. Use it when a release introduces a critical issue that must be reverted before a fix is available.

## Overview

A Hermit release publishes artifacts to multiple channels:

| Channel | Artifact | Rollback Difficulty |
|---------|----------|---------------------|
| GitHub Releases | `.whl`, `.tar.gz`, `.dmg` | Easy |
| PyPI | `hermit-agent` package | Medium (yank only) |
| Homebrew | `heggria/tap/hermit-agent` formula | Easy |
| GHCR | `ghcr.io/heggria/hermit` image | Easy |

Rollback all channels in the order listed below to minimize user impact.

---

## 1. Git Tag Rollback

Remove the bad tag and optionally re-tag at the known-good commit.

```bash
# Delete the bad tag locally and remotely
git tag -d v0.2.6
git push origin :refs/tags/v0.2.6

# (Optional) Re-tag at the previous known-good commit
git tag -a v0.2.6 <good-commit-sha> -m "Release v0.2.6 (rollback)"
git push origin v0.2.6
```

!!! warning
    Deleting a pushed tag rewrites public history. Coordinate with the team before proceeding.

---

## 2. GitHub Release Rollback

1. Navigate to [Releases](https://github.com/heggria/Hermit/releases).
2. Find the bad release and click **Delete**.
3. If re-tagging (step 1), the release workflow will create a new release automatically.

Alternatively, use the `gh` CLI:

```bash
gh release delete v0.2.6 --yes
```

---

## 3. PyPI Rollback

PyPI does not support deleting published packages. Use **yank** to mark a version as unsuitable:

```bash
# Yank the bad version (requires PyPI API token with project scope)
uv run twine yank hermit-agent 0.2.6

# Or via the PyPI web UI:
# https://pypi.org/manage/project/hermit-agent/releases/
```

A yanked version is still installable with `==0.2.6` but will not be selected by default version resolution.

---

## 4. Homebrew Rollback

Revert the formula commit in the tap repository:

```bash
git clone https://github.com/heggria/homebrew-tap.git
cd homebrew-tap

# Find and revert the bad formula commit
git log --oneline Formula/hermit-agent.rb
git revert <bad-commit-sha>
git push
```

Users running `brew upgrade` will downgrade to the previous version.

---

## 5. GHCR Image Rollback

Re-tag the known-good image as `latest`:

```bash
# Pull the known-good version
docker pull ghcr.io/heggria/hermit:0.2.5

# Re-tag as latest
docker tag ghcr.io/heggria/hermit:0.2.5 ghcr.io/heggria/hermit:latest
docker push ghcr.io/heggria/hermit:latest
```

Optionally delete the bad version tag via the GitHub Packages UI:

1. Go to `https://github.com/heggria/Hermit/pkgs/container/hermit`.
2. Find the bad version tag and delete it.

Or via `gh` CLI:

```bash
gh api -X DELETE /orgs/heggria/packages/container/hermit/versions/<version-id>
```

---

## Emergency Rollback Checklist

Use this checklist when performing a full rollback across all channels:

- [ ] **Git tag**: Delete bad tag, optionally re-tag at known-good commit
- [ ] **GitHub Release**: Delete the release page
- [ ] **PyPI**: Yank the bad version with `twine yank`
- [ ] **Homebrew**: Revert the formula commit in `heggria/homebrew-tap`
- [ ] **GHCR**: Re-tag known-good image as `latest`, delete bad tag
- [ ] **Communication**: Post in Discussions / notify users about the rollback
- [ ] **Post-mortem**: Document what went wrong and how to prevent recurrence
