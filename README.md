# redstonegis.com

Source for redstonegis.com. Every push to `main` uploads changed files to DreamHost
(`.github/workflows/deploy.yml`). Nothing on the server is ever deleted by a deploy.

Folders listed in `.deployignore` (e.g. the GLO map tiles in `GLO/d/`, `GLO/n/`) are
uploaded by hand and never touched by the deploy.

Required repo secrets: `DEPLOY_KEY` (private SSH key), `DEPLOY_HOST`, `DEPLOY_USER`.
