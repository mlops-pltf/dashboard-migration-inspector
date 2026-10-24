# Inspector: Design

The design now lives in the documentation site, under `site/src/content/docs/` (one MDX page per topic).

- Run locally: `docker compose --profile docs up -d --build docs` → http://localhost:4321
- Share (stable URL): `nohup ngrok http 4321 > /tmp/ngrok.log 2>&1 &` → https://uncoaxial-unobjectionable-gaylene.ngrok-free.dev
- Share (temporary URL): `docker compose --profile docs up -d docs-tunnel`, then `docker compose logs docs-tunnel | grep trycloudflare.com`
- Edit: change the MDX pages; `cd site && npm run dev` for live reload.
