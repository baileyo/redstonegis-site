# redstonegis.com

Source for redstonegis.com. Every push to `main` uploads changed files to DreamHost
(`.github/workflows/deploy.yml`). Nothing on the server is ever deleted by a deploy.

Paths listed in `.deployignore` are never touched by the deploy.

The GLO map's tiles (`GLO/d/`) and name index (`GLO/n/`) are about 300 MB, so only the files
changed since the first hand upload are committed here (the Texas update of November 2026).
Everything else in those folders lives only on the server and stays there.

Required repo secrets: `DEPLOY_KEY` (private SSH key), `DEPLOY_HOST`, `DEPLOY_USER`.
