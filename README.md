# Gurux AMI Catalog

Public catalog and RSS feed for Gurux DLMS AMI modules, applications, agents and configuration data.

GitHub Actions generates the catalog and publishes it to GitHub Pages. Module ZIP packages remain in their source repositories' GitHub Releases.

## Published files

- [catalog.json](https://gurux.github.io/Gurux.AMI.Catalog/catalog.json) — products, versions and download links.
- [rss.xml](https://gurux.github.io/Gurux.AMI.Catalog/rss.xml) — the ten latest releases with release notes.

The RSS feed lists releases across products in publication order, newest first. Generated configuration index pointers (`tagName: index`) are excluded: their synthetic version and generation time do not represent a new release.
- [localization/localization-index.json](https://gurux.github.io/Gurux.AMI.Catalog/localization/localization-index.json) — localization releases (initially empty).

The generator copies the `localization/` directory beside `catalog-sources.json` into the Pages output, including its packages. Keep the index and the packages referenced by its relative paths in that directory so every deployment publishes them together.

Script publication works similarly: keep `scripts/script-index.json` and its referenced source files under `scripts/files/` in the repository. The generator copies both into the Pages output, excluding Python build tools. The initial script index is empty.

## Module icons

Store each icon with its module source, for example
`Gurux.AMI.SMTP/assets/Smtp.svg` in `Gurux/Gurux.AMI.Modules`. Configure its
repository-relative source path and public Catalog URL in `catalog-sources.json`:

```json
"iconPath": "Gurux.AMI.SMTP/assets/Smtp.svg",
"iconUrl": "https://gurux.github.io/Gurux.AMI.Catalog/assets/Gurux.AMI.SMTP/Smtp.svg"
```

Each Catalog publication downloads this file from the module's selected release
tag and writes `_site/assets/<module-id>/<filename>`. No copy in the Catalog
repository is needed. The latest stable release among the catalog's selected
releases supplies the icon; if only prereleases are selected, the latest one is
used. Without a published release, the generated entry omits `iconUrl`.

Commit an updated icon in the Modules repository and publish a new module
version. Its existing Catalog notification rebuilds the catalog and fetches the
new icon automatically. Changing an icon on a branch without publishing a module
release does not update the public icon. Generated URLs include a content hash
query so clients refresh changed images.

Supported extensions are SVG, PNG, JPEG, WebP, GIF and ICO, with a 1 MiB limit.
Paths must remain repository-relative. An icon fetch or validation failure stops
publication and preserves the currently deployed site. Existing manually managed
Catalog `assets/` files still work for entries without `iconPath`.

`Gurux.Updater.Tool register-module` discovers a single image in the project's
`assets` directory and writes these fields automatically. With multiple images,
select one using `--icon assets/Smtp.svg`. Existing curated icon settings are
preserved unless `--icon` is supplied.

## Module compatibility

Modules configured with `compatibilityMetadata: true` supply a versioned `.compatibility.json` release asset. Catalog validates it and includes the required host DLL versions for AMI's pre-install compatibility check. Missing or invalid metadata preserves the previously published catalog.

The same sidecar can declare an optional `amiVersionRange`, shown as **Works with AMI**.
This is a maintainer-tested product version range, separate from exact DLL requirements:
`"amiVersionRange": "[4.1.2610.2701,5.0)"`. The lower bound is inclusive and the upper
bound exclusive. Exact `[4.1.2610.2701]`, minimum `4.1.2610.2701`, open bounds and `*`
are also supported. Missing data means unknown compatibility. Do not infer this range
from assembly references. Each release keeps its own range, including local uploads.

## Project classification

Project maintainers edit these optional fields in `catalog-sources.json` when registering
a product or when its purpose or support changes. Regenerate the catalog afterwards;
no module rebuild or new release is needed:

```json
"maintenanceStatus": "active",
"category": "communication",
"audience": "operator"
```

`maintenanceStatus` accepts `active`, `minimal`, `seeking-maintainer` or `unsupported`.
It describes the project, independently of any release's support or revocation flags.
`category` describes function (for example `communication`, `diagnostics`, `reporting`);
`audience` describes intended users (for example `developer`, `operator`, `administrator`).
Both are lowercase slugs, with optional hyphens, up to 100 characters. Omitted fields
remain unknown; old catalog sources continue working. The generator preserves these
fields for modules, applications, agents and configuration entries.

Use the sibling Updater Tool to filter products; filters combine with AND:

```powershell
dotnet run --project ../Gurux.Updater.Net/Development/Gurux.Updater.Tool -- check --catalog-url https://gurux.github.io/Gurux.AMI.Catalog/catalog.json --maintenance-status active --category communication --audience operator --json
```

## Content releases: tags, GitHub Releases and Catalog

Automated publication uses the existing chain:

`source change -> independent annotated tag -> GitHub Release -> repository_dispatch -> Catalog -> Pages`.

The source repository allocates content versions. Catalog only imports already
published versions and never creates content tags or regenerates packages from
moving source directories. The previous `localization-sources.json` preparation
commands remain available for local/offline work; Catalog CI no longer uses them.

### Source registration and tags

Each source repository registers its own `content-sources.json`:

```json
{
  "sources": [
    {
      "kind": "localization", "directory": "localization",
      "owner": "Gurux.AMI.SMTP", "ownerType": "module",
      "defaultCulture": "en", "ownerVersionRange": "[1.0.0,2.0.0)"
    },
    {
      "kind": "resources", "directory": "resources/templates",
      "owner": "GXDLMSDirector", "ownerType": "application", "name": "templates"
    }
  ]
}
```

Directories are explicit and resolve beside this configuration. Localization uses
the shared C# project/package metadata inference or the explicit overrides above.
Resources are one owner's named resource directory, including its files recursively;
they are separate from device-profile/script configuration distributions. Resource
owner/type/name are explicit. Every release input must stay inside the tagged source
repository; symbolic links, traversal and case-colliding/reserved filenames fail.
Do not register generated output or version-control directories as resource sources.

Tag formats and published catalog paths:

| Kind | Tag | Catalog package |
| --- | --- | --- |
| Localization | `localization/<owner>/<culture>/v<version>` | `localization/packages/<owner>/<culture>/<version>.json` |
| Resource directory | `resources/<owner>/<name>/v<version>` | `resources/packages/<owner>/<name>/<version>.zip` |

For example `localization/Gurux.AMI.SMTP/fi/v1.0.1` and
`resources/GXDLMSDirector/templates/v1.0.0`. These namespaces do not match existing
binary-module tags (`Gurux.AMI.*/v*`); the module release workflow is unchanged.

Both units start at `1.0.0` and increment only their own patch number. Localization
uses canonical parsed JSON/resources/metadata hashes; formatting or key order do
not count as changes. Resources hash ordinally sorted relative filenames, exact file
bytes and owner/type/name/compatibility metadata. File timestamps and generated version
are excluded. Deterministic ZIPs preserve exact resource bytes and include generated
`resource-package.json`; this filename is reserved. New/removed languages never remove
published packages or installed translations. No changed content means no new tag.

The annotated tag message is the complete immutable `release-manifest.json`, including
identity, version, canonical content SHA-256, artifact size/SHA-256 and derived paths.
A tag therefore reserves a version before uploading succeeds. The source workflow
serializes publication for the repository, fetches tags, completes unpublished reserved
tags first, and only then plans new changes. Conflicting tag pushes fail; they never
force-update a tag. Incomplete tag retries rebuild from that tag's pinned commit, verify
its exact expected bytes and allocate no version. An already published tag uses
its verified existing assets, so notification retries need no source rebuild. Do not create lightweight content
tags, change tag messages, or overwrite published assets.

### Install the source workflow

Updater supplies `.github/workflows/release-content.yml` as a reusable workflow and
`Development/examples/content/publish-content.yml` as a caller example. The Updater
repository has its own installed caller and an initially empty
`Development/content-sources.json`; enable only real source directories. Other source
repositories place the caller under `.github/workflows/` and adjust `paths` to their
registered source directories and project metadata. The example caller uses
`localization/**`, `resources/**` and `content-sources.json`. Extend those filters when
metadata lives elsewhere. Source project/package metadata changes that affect packages
must trigger detection too.

The caller needs `contents: write`, and repository secret `CATALOG_DISPATCH_TOKEN`
with Contents:write permission on Gurux/Gurux.AMI.Catalog for cross-repository dispatch.
The source repository's own GITHUB_TOKEN creates its tags/Releases. Bot-created tags
do not start another workflow with GITHUB_TOKEN: the detecting job immediately runs
the tag's Release stage itself. Human tag pushes and `workflow_dispatch` use the same
stage. Thus the chain works without relying on a suppressed bot push event.

The Release stage verifies local artifacts, creates a draft if necessary, uploads the
package plus `release-manifest.json`, publishes the draft, re-reads the published
Release, downloads and verifies both assets, and only then sends `localization-release`
or `resources-release` to Catalog. A failed upload/publish/verification sends no
notification. Once published, assets are not uploaded again. A notification failure
fails the job with the Release retained. Retry the same tag to repeat notification:

```powershell
gh workflow run publish-content.yml --repo Owner/Source -f tag=localization/Gurux.AMI.SMTP/fi/v1.0.1
```

For changed-content detection without a supplied tag:

```powershell
gh workflow run publish-content.yml --repo Owner/Source
```

The workflow's publisher implementation is built from Gurux/Gurux.Updater.Net main;
merge that implementation before enabling consumers. Existing catalog-only language
history must be aligned with source release/tag history before enabling automatic tag
allocation; immutable conflicting versions are deliberately rejected rather than replaced.

### Catalog import and durable history

`content-release-sources.json` allowlists source repositories. It currently registers
Updater and the existing AMI module repository. Add another source repository before
letting it notify Catalog. Dispatch payloads do not authorize arbitrary repository
access or package URLs. For each event Catalog fetches the specific Release through
GitHub, rejects drafts/prereleases/incomplete assets, checks legacy annotated tags
where required, and invokes C# to verify and import the package. Duplicate events perform
an idempotent import of that same version; Catalog never increments versions.
Scheduled/manual/module-triggered catalog runs reconcile published content Releases
from registered repositories, recovering missed notifications. Pull requests do not
import remote releases or publish history.

History remains Git-tracked under `localization/` and `resources/`. Localization
packages use schemaVersion 2, flat resources and one culture; their shared index keeps
the existing schemaVersion 1 items/releases structure with culture/contentSha256.
`resources/resource-index.json` uses schemaVersion 1 with `items` containing immutable
release manifests (owner/type/unit/version/path/size/sha256/contentSha256/tag).
The resource index is registered in the main catalog as `Gurux.Resources`.

C# validates entire artifacts before writing, locks the index, retains old versions,
installs immutable packages and replaces the index last. Repeat imports preserve
index bytes/timestamps. `.gitattributes` disables line-ending conversion for both
kinds of packages. `_site` and caches are never version authorities. Artifact copying
captures one index snapshot, installs complete files without truncating served files,
then installs the captured index. Validation fails if an index references missing or
invalid packages. CI commits both kinds of history before deploying one complete Pages
artifact, serialized with the existing catalog concurrency group. Contents:write and
branch policy must permit the bot history commit; a rejected push prevents deployment.

Local reconciliation and validation, from the Catalog repository:

```powershell
dotnet build ../Gurux.Updater.Net/Development/Gurux.Updater.Tool -c Release
$env:GURUX_LOCALIZATION_VALIDATOR = (Resolve-Path ../Gurux.Updater.Net/Development/Gurux.Updater.Tool/bin/Release/net10.0/Gurux.Updater.Tool.dll).Path
python scripts/import_content_releases.py --repository Gurux/Gurux.DLMS.AMI.Modules --tag localization/Gurux.AMI.SMTP/fi/v1.0.1
dotnet $env:GURUX_LOCALIZATION_VALIDATOR localization validate-store --index localization/localization-index.json
dotnet $env:GURUX_LOCALIZATION_VALIDATOR content validate-resources --catalog-root .
python scripts/build_catalog.py --output _site/catalog.json
git add -- localization/ resources/
git commit -m "Import published content releases"
git push origin main
```

GitHub authentication for `gh` is required. These are maintainer commands; implementation
tests do not create live tags, Releases or notifications. Pages accepts no uploads.
The local server can still accept direct source snapshots for offline/local testing,
but these do not automatically create source GitHub Releases or tags. Publish production
content through the source workflow rather than mixing independent version authorities.

### Independent module localization and assets

Module bundles published by `Gurux/Gurux.AMI.Modules` use independent versions:

| Content | Exact tag | Release package |
| --- | --- | --- |
| All module languages | `Gurux.AMI.SMTP/localization/v1.0.0` | `Gurux.AMI.SMTP-localization-1.0.0.json` |
| Module assets | `Gurux.AMI.SMTP/assets/v1.0.0` | `Gurux.AMI.SMTP-assets-1.0.0.zip` |

These tags may be lightweight or ordinary annotated commit tags. Each published
stable Release must upload `release-manifest.json` and its exact package. The manifest
requires `modulePackage: true`, `ownerType: "module"`, the matching module ID as
`owner`, and a nonempty `ownerVersionRange`. Localization uses `kind: "localization"`,
`unit: "all"` and one schemaVersion 1 package containing all languages. Assets use
`kind: "resources"`, `unit: "asset"` and the existing resource ZIP format. Catalog
validates the complete manifest with the shared Updater engine before downloading
its package; existing versions cannot be replaced with different bytes.

This module convention is separate from the legacy `localization/owner/culture/v1.0.0`
and `resources/owner/unit/v1.0.0` conventions, whose annotated JSON tag manifests remain
required. Binary module tags such as `Gurux.AMI.SMTP/v1.0.0` are excluded from content
imports. Module localization/assets tags are excluded from binary catalog releases.

Before enabling publication, merge the supporting Updater validator and Modules
publisher changes, enable the Modules content workflow, and configure its
`CATALOG_DISPATCH_TOKEN` with Contents:write access to this Catalog repository.
`content-release-sources.json` allows `Gurux/Gurux.AMI.Modules`; binary module
sources use the same repository. Register only existing accessible repositories:
scheduled reconciliation queries every entry in this allowlist.
The source publisher sends `localization-release` or `resources-release` with
`repository`, `module`, `packageType` and `tag`; Catalog retrieves the Release from
the allowlisted repository. Scheduled reconciliation also imports missed releases.

```powershell
python scripts/import_content_releases.py --repository Gurux/Gurux.AMI.Modules --tag Gurux.AMI.SMTP/localization/v1.0.0
python scripts/import_content_releases.py --repository Gurux/Gurux.AMI.Modules --tag Gurux.AMI.SMTP/assets/v1.0.0
```

### Compatibility and application integration

SchemaVersion 1 all-language packages remain readable/updatable, and the older CLI
commands keep their meaning. When an owner adopts schema 2 languages, new legacy
uploads are rejected and legacy installations require explicit migration to separate
culture files. The updater never compares an all-language package version with an
independent language version. Downloaded originals remain separate from user-specific
translation overrides; fallback/database merging belongs to the host application.

CLI/library language download/update still checks compatibility, size/SHA-256, schema,
identity, version and metadata, with atomic JSON/ZIP replacement and per-culture results.
See the Updater README for direct local commands and the content-source example for
both localization and resource-directory registration.

Regression commands:

```powershell
dotnet test ../Gurux.Updater.Net/Tests/ModuleContent.Tests -c Release -p:GeneratePackageOnBuild=false
python -m unittest discover -s ../Gurux.Updater.Net/Development/tests-content-release -v
python -m unittest discover -s tests -v
```
