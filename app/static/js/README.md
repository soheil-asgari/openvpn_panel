# Vendored front-end libraries

The panel loads two small third-party libraries from this directory:

- `htmx.min.js` — [htmx](https://htmx.org/) (BSD 2-Clause)
- `chart.min.js` — [Chart.js](https://www.chartjs.org/) (MIT)

They are **prebuilt assets**: no Node.js toolchain is required on user servers.

## How they are provided

- `scripts/fetch-assets.sh` downloads the pinned versions from their official
  CDNs.
- The **release build** (`scripts/release/build-release.sh`) runs that script
  before packaging, so every released tarball contains the assets. CI does the
  same in the `install-smoke` job.
- The installer never runs a JavaScript build step.

If the files are missing in a development checkout, run:

```bash
./scripts/fetch-assets.sh
```

The application degrades gracefully if they are absent: navigation and forms
still work as plain HTML — only partial updates and the dashboard chart are
disabled.
