# Research and community acknowledgments

Project Swallow is inspired by the long history of Ace Combat preservation and fan translation work. This file records potential relevant research; it does **not** imply endorsement, partnership, or incorporation of any third-party code.

## Thank you, Project Nemo

[Project Nemo](https://projectnemo.net/wiki/index.php/ProjectNemo/Home) is the
unofficial, non-profit, all-volunteer fan project that has been translating
*Ace Combat 3: Electrosphere* since 2009. Its own [About page](https://projectnemo.net/wiki/index.php/ProjectNemo/About)
records:

- years of game-data digging and hacking (2010–2012);
- the International Edition (2016), with English subtitles for all 52 missions on both discs;
- a talk at OggCamp 2017;
- the v0.9b Fan Translation (2023).

Their work kept the complete Japanese Electrosphere playable and understood by
English-speaking players for over a decade, and Project Swallow's goal of an
English Electrosphere follows the path they opened. Thank you to the crew as
listed by the project:

- **DragonSpikeXIII**: co-founder, project manager, blogger
- **Imran Chaudhry / Iceman-UK**: co-founder, webmaster, sysadmin
- **BRPXQZME**: translator
- **pmt7ar**: translator
- **Greenrose**: translator, quality assurance
- **esperknight**: game data mining
- **Dashman**: game tools engineer
- **Gipphe**: coder

The English-labeled reference discs in this repository carry a "v0.9b3"
upload label, consistent with Project Nemo's v0.9b fan-translation naming.
The project has **not independently verified** who produced those images, so
measurements of them are not presented as statements about Project Nemo's
release. No Project Nemo code, tools, or translated text have been
incorporated into Project Swallow. Any future use of their translation or
tools will be discussed with the team first.

Support them: [download](https://projectnemo.net/wiki/index.php/ProjectNemo/Download) ·
[donate](https://projectnemo.net/wiki/index.php/ProjectNemo/Donate) ·
[contact](https://projectnemo.net/wiki/index.php/ProjectNemo/Contact).

## Other research and references

- Load Word Team — complete AC3 fan-translation research and revision fingerprints: https://loadwordteam.com/projects/ace-combat-3-electrosphere-a-complete-fan-translation/
- Krishty — research notes on AC3 archives: https://www.zfx.info/viewtopic.php?p=53328
- Redump — optical disc identification references: https://redump.info/

Any imported code or assets require separate license/provenance review and any necessary permissions. Content is not automatically covered by Project Swallow's MIT license.

- psx-spx / PlayStation Specifications — CD sector layout and XA documentation: https://psx-spx.consoledev.net/cdromformat/
- psx-spx / PlayStation Specifications — AC3 BPH/SPH archive facts checked against the supplied images: https://psx-spx.consoledev.net/cdromfileformats/
- psx-spx / PlayStation Specifications — Namco "Ulz" compression layout and decoding steps, and the DOT1 offset-table description, both checked against every measured AC3 BPB block (same page: "CDROM File Compression Ulz/ULZ (Namco)" and "CDROM File Archives with Offset"). The decoder is original project code.
- GitHub Actions official documentation — current action usage referenced by the workflow: https://github.com/actions/checkout and https://github.com/actions/setup-python

English-labeled v0.9b3 source uploads are recorded as local evidence; this package does not establish their translation authorship or conflate them with other patch releases. No third-party implementation code was imported into this update.
