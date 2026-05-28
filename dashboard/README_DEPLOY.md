# Dashboard online deploy

## Recommended free host

Use Vercel for the first online version.

- Project root: this `dashboard/` folder
- Build command: empty / none
- Output directory: `.`
- Framework preset: Other

This deploy is static, so the link stays open even when the local machine is off. Data updates when the generated files in this folder are redeployed.

## Files required

- `index.html`
- `styles.css`
- `app.js`
- `data.js`
- `analysis.js`
- `history.js`
- `vercel.json`

## Later live automation

For automatic data refresh while the local machine is off, add a remote scheduled job such as GitHub Actions that regenerates `data.js`, `analysis.js`, and `history.js`, then redeploys Vercel.
