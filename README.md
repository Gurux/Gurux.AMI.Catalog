# Gurux AMI Catalog

Public catalog and RSS feed for Gurux DLMS AMI modules, applications, agents and configuration data.

GitHub Actions generates the catalog and publishes it to GitHub Pages. Module ZIP packages remain in their source repositories' GitHub Releases.

## Published files

- [catalog.json](https://gurux.github.io/Gurux.AMI.Catalog/catalog.json) — products, versions and download links.
- [rss.xml](https://gurux.github.io/Gurux.AMI.Catalog/rss.xml) — the ten latest releases with release notes.

## Module compatibility

Modules configured with `compatibilityMetadata: true` supply a versioned `.compatibility.json` release asset. Catalog validates it and includes the required host DLL versions for AMI's pre-install compatibility check. Missing or invalid metadata preserves the previously published catalog.
