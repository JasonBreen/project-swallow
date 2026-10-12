# Contributing

We welcome code, documentation, verified binary-format findings, original visual assets and human-performed English dialogue. All new contributions must have a clear provenance and suitable distribution rights.

## For code contributors and AI coding agents

1. Keep PRs narrowly scoped; describe actual observations separately from assumptions.
2. Add tests for successful parsing, corrupt input, and boundary conditions.
3. The owner-approved full reference archives are stored under `discs/` using Git LFS in this private repository. Record hashes when adding a supplied reference. Keep generated raw extracts and unrelated assets out of commits; the project's MIT license does not cover third-party disc contents.
4. Include commands required to reproduce test results and note which disc revision was tested.
5. An automated agent may propose changes, but a human must review and approve merges.
6. Keep external tool licenses and notices intact; do not assume every upstream project is MIT.

## Audio and artwork

Before recording or submitting artwork, agree on character assignments, delivery formats, permissions, credit preferences, and intended license. Do not upload unfinished private recordings to public Git history.

## Test

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```
