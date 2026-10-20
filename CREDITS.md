# Research and community acknowledgments

Project Swallow is inspired by the long history of Ace Combat preservation and fan translation work. This file records potential relevant research; it does **not** imply endorsement, partnership, or incorporation of any third-party code.

## Thank you, Team NEMO (Project NEMO)

[Team NEMO](https://www.romhacking.net/community/1189/) is the group behind
Project NEMO, a fan translation of *ACE COMBAT 3 electrosphere* (1999). Its
Romhacking.net page describes it as "mainly a US/UK joint effort" with
contributions from around the world. Their English fan translation is listed
on Romhacking.net as [Ace Combat 3: Electrosphere (EN)](https://www.romhacking.net/translations/2307/),
"Fully Playable", updated 03 Mar 2026. They also released the
[M27 BGM fix](https://www.romhacking.net/hacks/8281/) (07 Dec 2023).

For well over a decade, their work has let English-speaking players experience
the complete Japanese Electrosphere. Project Swallow's goal of an English
Electrosphere follows the path they opened. Thank you to the crew, credited
here exactly as on Romhacking.net:

| Contributor | Credited role |
|---|---|
| **DragonSpikeXIII** | Project Manager / Lead Translator |
| **iceman-uk** | Support / Webmaster |
| **BRPXQZME** | Translator, Mission and Archive Drafts (2009-10) |
| **pmt7ar** | Translator, Epilogue Draft (2009-10) |
| **Gipphe** | Hacker, Data Mining (2011) |
| **esperknight** | Hacker, AC3 Toolkit, Decompression (2014-16) |
| **Dashman** | Hacker, Tooling, Original Typesetter (2014-16) |
| **Greenrose** | Translator, Retranslations for v2.0 (2016) |
| **weissvulf** | Hacker/QA/Tooling/Typesetter (2018) |

esperknight's decompression work and the team's AC3 toolkit are prior art for
the archive and Ulz work in this repository. Project Swallow's decoder was
written independently from public format documentation (psx-spx) and measured
bytes. No Team NEMO code, tools, or translated text have been incorporated. Any
future use of their translation or tooling will be discussed with the team
first.

The English-labeled reference discs in this repository carry a "v0.9b3"
upload label. According to Romhacking.net search listings, Team NEMO's current
translation release is version 0.9b3 ("Warchild"), distributed as xdelta
patches for the original SKU. The direct Romhacking.net page was behind a bot
check and could not be read for this update. The project has therefore **not
verified** that these images were produced with Team NEMO's patch, and
measurements of them are not presented as statements about Team NEMO's release.

## Other research and references

- Load Word Team — complete AC3 fan-translation research and revision fingerprints: https://loadwordteam.com/projects/ace-combat-3-electrosphere-a-complete-fan-translation/
- Krishty — research notes on AC3 archives: https://www.zfx.info/viewtopic.php?p=53328
- Redump — optical disc identification references: https://redump.info/

Any imported code or assets require separate license/provenance review and any necessary permissions. Content is not automatically covered by Project Swallow's MIT license.

- psx-spx / PlayStation Specifications — CD sector layout and XA documentation: https://psx-spx.consoledev.net/cdromformat/
- psx-spx / PlayStation Specifications — AC3 BPH/SPH archive facts, the `DOT1` offset-table shape, the Namco Ulz version 0/2 flag-word description, and the Sony TIM texture-image layout (section "CDROM File Video Texture Image TIM/PXL/CLT"), each checked against the supplied images: https://psx-spx.consoledev.net/cdromfileformats/
- GitHub Actions official documentation — current action usage referenced by the workflow: https://github.com/actions/checkout and https://github.com/actions/setup-python

English-labeled v0.9b3 source uploads are recorded as local evidence; this package does not establish their translation authorship or conflate them with other patch releases. No third-party implementation code was imported into this update.
