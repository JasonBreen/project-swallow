# Reference discs

The owner requested that the full supplied disc archives be included in this
private GitHub repository using suitable large-file storage on 2026-10-12.
Git LFS stores the complete archives; Git commits contain their small pointer
files. This supersedes the earlier starter's recommendation to keep all source
discs outside Git.

## Included inputs

| Input | Archive | Compressed bytes |
|---|---|---:|
| English v0.9b3-labeled Disc 1 | `english-v0.9b3/Ace Combat 3 Electrosphere (English v0.9b3)[Disc 1].tar.bz2` | 451,360,415 |
| English v0.9b3-labeled Disc 2 | `english-v0.9b3/Ace Combat 3 Electrosphere (English v0.9b3)[Disc 2].tar.bz2` | 459,268,683 |
| Japanese Disc 1 | `japanese/Ace Combat 3 - Electrosphere (Japan) (Disc 1).7z` | 377,176,022 |
| Japanese Disc 2 | `japanese/Ace Combat 3 - Electrosphere (Japan) (Disc 2).7z` | 380,185,348 |

`manifest.json` records SHA256 for each complete archive and the sizes and
hashes of its BIN/CUE members. Each archived BIN was streamed and checked
against the corresponding existing `docs/reports/en1.json` or `en2.json`
baseline before upload. The compressed originals are preserved byte for byte.
The v0.9b3 description remains a source label, not independently verified
translation authorship or release identity.

The supplied Japanese BINs match the earlier `jp1`/`jp2` reports by size, MD5,
SHA-1, and SHA-256. This establishes the same baseline, not an independent
Redump database certification. The complete original 7z archives include a
`readme.html` member, preserved without execution. Their CUE descriptors still
refer to older BIN filenames; read the BIN directly for these tools.

## Retrieve and inspect

Install Git LFS, then run `git lfs pull` in a normal clone. Use `git lfs ls-files`
to list the tracked archives and `git lfs fsck` to check the local LFS objects.
The total compressed size of all four archives is 1,667,990,468 bytes.

Extract each archive into a separate local directory, preferably on a drive
with room for both full BINs. For example, on Windows with the built-in `tar`:

```powershell
New-Item -ItemType Directory -Force 'G:\ProjectSwallow-data\english-disc1'
tar -xjf 'discs/english-v0.9b3/Ace Combat 3 Electrosphere (English v0.9b3)[Disc 1].tar.bz2' -C 'G:\ProjectSwallow-data\english-disc1'
$env:PYTHONPATH = 'src'
python -m swallow_tools 'G:\ProjectSwallow-data\english-disc1\Ace Combat 3 Electrosphere (English v0.9b3)[Disc 1].bin' --hash --file-hashes --json
```

Repeat for Disc 2 with its corresponding names. The toolkit reads BIN files,
not compressed archives. Keep extraction outputs outside the tracked archive
directory; changing or regenerating an original archive creates another LFS
object.

For the Japanese 7z archives, use 7-Zip and extract to separate directories
on `G:`. The BPH command accepts each extracted BIN directly:

```powershell
python -m swallow_tools.bph 'G:\ProjectSwallow-data\japanese-disc1\Ace Combat 3 - Electrosphere (Japan) (Disc 1).bin' --json
```

For a tooling-only clone in PowerShell, skip automatic LFS downloads:

```powershell
$env:GIT_LFS_SKIP_SMUDGE = '1'
git clone https://github.com/JasonBreen/project-swallow.git
Remove-Item Env:GIT_LFS_SKIP_SMUDGE
```

CI explicitly uses `lfs: false` and synthetic fixtures so normal test runs do
not download the discs. GitHub's source ZIP is not the verification path for
these archives; use an authenticated clone with Git LFS.

## License scope

The project's MIT license applies to its original tools, tests, and
documentation. It does not relicense the game, translation, or other
third-party material contained in these archives.
