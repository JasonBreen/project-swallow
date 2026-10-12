"""Content-addressed local baselines; filenames never establish identity."""
from __future__ import annotations
from typing import Any

PROFILES: dict[str, dict[str, Any]] = {'cfdec8640cfb0fe5c1ad1ad8c76c958d756fbe75d73e1f449d468ebbafb8d60f': {'id': 'jp1',
                                                                      'disc_number': 1,
                                                                      'label': 'AC3 Japanese Disc '
                                                                               '1, LWT Revision 1 '
                                                                               'fingerprint',
                                                                      'md5': '30f7dce98b6901290cb26c9baf27268f',
                                                                      'size_bytes': 716287488,
                                                                      'provenance': 'Matches Load '
                                                                                    'Word Team '
                                                                                    'published '
                                                                                    'MD5; SHA256 '
                                                                                    'measured '
                                                                                    'locally.'},
 'cfd9cd36ba858aa81927306ec761a228c8e88be48f1b1db9ac9d7f2bdbe8c854': {'id': 'jp2',
                                                                      'disc_number': 2,
                                                                      'label': 'AC3 Japanese Disc '
                                                                               '2, LWT Revision 1 '
                                                                               'fingerprint',
                                                                      'md5': '50760963ae8a26ea0188b165f05b33a1',
                                                                      'size_bytes': 723813888,
                                                                      'provenance': 'Matches Load '
                                                                                    'Word Team '
                                                                                    'published '
                                                                                    'MD5; SHA256 '
                                                                                    'measured '
                                                                                    'locally. Disc '
                                                                                    '2 fingerprint '
                                                                                    'is also '
                                                                                    'listed for '
                                                                                    'the Asian '
                                                                                    'release.'},
 '4a6aca534e396236e704886cb1f7000ee64012910435d1beb3e65a0ab1369d80': {'id': 'en1',
                                                                      'disc_number': 1,
                                                                      'label': 'AC3 Disc 1, '
                                                                               'uploaded English '
                                                                               'v0.9b3 label',
                                                                      'md5': 'deca6b0cf45d4358a3ec1a13817df04a',
                                                                      'size_bytes': 715238496,
                                                                      'provenance': 'Local '
                                                                                    'reference '
                                                                                    'only. '
                                                                                    'Translation '
                                                                                    'identity/version '
                                                                                    'is taken from '
                                                                                    'the upload '
                                                                                    'label, not '
                                                                                    'independently '
                                                                                    'authenticated.'},
 '4d7297fa25d8aacf828c54c8168a62f9dc577506b4644f706589ced0b85ceffe': {'id': 'en2',
                                                                      'disc_number': 2,
                                                                      'label': 'AC3 Disc 2, '
                                                                               'uploaded English '
                                                                               'v0.9b3 label',
                                                                      'md5': '0193416e6167c50b2b59f816703cb9ac',
                                                                      'size_bytes': 722936592,
                                                                      'provenance': 'Local '
                                                                                    'reference '
                                                                                    'only. '
                                                                                    'Translation '
                                                                                    'identity/version '
                                                                                    'is taken from '
                                                                                    'the upload '
                                                                                    'label, not '
                                                                                    'independently '
                                                                                    'authenticated.'}}

def identify_disc(hashes: dict[str, str], size_bytes: int) -> dict[str, Any] | None:
    profile = PROFILES.get(hashes.get('sha256', ''))
    if profile is None or profile['size_bytes'] != size_bytes or profile['md5'] != hashes.get('md5'):
        return None
    return dict(profile)
