# Example world configs

These are sample **world-generation configs** in the format the New-World / restart system
uses (`WorldGenConfig`). Each file is the `config` object accepted by
`POST /api/world/restart`.

| File | What it makes |
|---|---|
| `default-world.json` | The balanced default world (seed 1337, 5 civs) |
| `five-civs.json` | Five rival nations, slightly larger starting population |
| `harsh-ice-age.json` | A frozen world — cold, dry, low fertility, snowy texture pack |
| `dense-cities.json` | High expansion/fertility and 8 civs for a crowded map |

## Use them

Via the API (wrap as `{"config": …}`):

```bash
curl -X POST localhost:8080/api/world/restart \
  -H 'content-type: application/json' \
  -d "{\"config\": $(cat examples/configs/harsh-ice-age.json)}"
```

Or copy the values into the **Setup → New World** panel and restart.

All fields are validated and clamped server-side; see [../../docs/WORLDGEN.md](../../docs/WORLDGEN.md)
for the full schema. Get the live schema any time with `GET /api/world/config/schema`.
