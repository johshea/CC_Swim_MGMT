# Catalyst Center SWIM Image Deletion Script

This guide explains how to use the `swim_delete_images.py` script to
delete software images from Cisco Catalyst Center (formerly DNA Center)
using the official SWIM APIs.

------------------------------------------------------------------------

## 🔧 Prerequisites

-   Python 3.8+ installed
-   Network access to your Catalyst Center (HTTPS/443)
-   Admin or Image Management privileges in Catalyst Center
-   An account with API access or a valid `X-Auth-Token`

Install dependencies automatically (script will handle it, but manual
install if desired):

``` bash
pip install requests
```

------------------------------------------------------------------------

## 🧭 Script Overview

The script connects to the Catalyst Center REST API and deletes SWIM
images that match your chosen filters (e.g., family, version, age,
unused, etc.).\
It supports dry-run mode, filtering, and optional golden-tag removal.

### Key Features

-   List and filter images by family, version, regex, type, or age
-   Delete unused or older images automatically
-   Optionally remove *golden tags* before deletion
-   Supports dry-run and confirmation prompts
-   Returns async task results

------------------------------------------------------------------------

## ⚙️ Basic Usage

Dry-run (no deletions, list matches only):

``` bash
python3 swim_delete_images.py   --base-url https://dnac.example.com   --username admin --password 'YourPassword'   --family cat9k --unused-only --older-than-days 180   --dry-run
```

Actual deletion (with confirmation prompt):

``` bash
python3 swim_delete_images.py   --base-url https://dnac.example.com   --username admin --password 'YourPassword'   --family cat9k --unused-only --older-than-days 180
```

Run without prompt (dangerous):

``` bash
python3 swim_delete_images.py ... --yes
```

------------------------------------------------------------------------

## 🧩 Filtering Options

  Option                  Description
  ----------------------- -----------------------------------------------------
  `--family`              Device family (e.g., `cat9k`, `asr1k`)
  `--version`             Exact software version
  `--version-regex`       Regex match for versions (e.g., `'^17\.9\.'`)
  `--name-contains`       Match images by substring
  `--name-regex`          Regex for image name
  `--type`                Filter by image type (`bin`, `smu`, `rommon`, etc.)
  `--older-than-days`     Only images older than N days
  `--unused-only`         Delete only unused images
  `--golden true/false`   Filter golden or non-golden images

------------------------------------------------------------------------

## 🏷️ Golden Tag Removal (Optional Pre-Step)

Some images cannot be deleted if tagged as *Golden*.\
You can remove the tag first by providing site/family/role info:

``` bash
python3 swim_delete_images.py   --base-url https://dnac.example.com   --username admin --password 'YourPassword'   --family cat9k --golden true   --site-id -1   --device-family-identifier 277696480   --device-role ALL   --yes
```

------------------------------------------------------------------------

## 🔒 Authentication

Provide credentials or an API token.

### Username/Password:

``` bash
--username admin --password YourPassword
```

### Token:

``` bash
--token <X-Auth-Token>
```

------------------------------------------------------------------------

## 🧠 API Target Information

  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  Method            Endpoint                                                                                                                        Description
  ----------------- ------------------------------------------------------------------------------------------------------------------------------- ----------------------------
  `GET`             `/dna/intent/api/v1/image/importation`                                                                                          List images

  `DELETE`          `/dna/intent/api/v1/image/importation/{imageId}`                                                                                Delete image

  `DELETE`          `/dna/intent/api/v1/image/importation/golden/site/{siteId}/family/{deviceFamilyIdentifier}/role/{deviceRole}/image/{imageId}`   Remove golden tag

  `GET`             `/dna/intent/api/v1/task/{taskId}`                                                                                              Check async task status
  ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**Note:** Some Catalyst Center builds may also support
`/dna/intent/api/v1/image/{imageId}` as a legacy delete path.

------------------------------------------------------------------------

## 🧾 Example Output

Example dry-run result:

    Candidates:
    - 2d1c6b98-8a5d-4a25-a3e9-2b5e2e1d1f3f  cat9k_iosxe.17.9.4a.SPA.bin  v17.9.4a  fam=cat9k  type=bin  golden=False  used=0

    Total matches: 1

    DRY RUN: no deletions performed.

------------------------------------------------------------------------

## ⚠️ Safety Tips

-   Always start with `--dry-run`.
-   Avoid `--yes` until you confirm filters.
-   Verify image is **not golden** before deletion.
-   Deleting images in use may disrupt SWIM operations.

------------------------------------------------------------------------

## 🧰 Troubleshooting

  ------------------------------------------------------------------------
  Error                Cause                  Solution
  -------------------- ---------------------- ----------------------------
  `409 Conflict`       Image is golden or in  Remove golden tag first
                       use                    

  `401 Unauthorized`   Bad credentials or     Re-authenticate
                       expired token          

  `404 Not Found`      Image already deleted  Re-run image list
                       or invalid UUID        

  `task timeout`       Task took too long     Check system health or retry
  ------------------------------------------------------------------------

------------------------------------------------------------------------

## 📘 References

-   [Cisco Catalyst Center Developer API
    Guide](https://developer.cisco.com/docs/dna-center/)
-   [Cisco SWIM API
    Reference](https://developer.cisco.com/docs/dna-center/#!software-image-management-swim-apis)
-   [Cisco DevNet
    Sandbox](https://developer.cisco.com/site/devnet/sandbox/)
