# Screenwall Makr — Streamlit deployment

**Release:** Beta v0.1.0 — First Draft (2026-05-15)

## Files that belong in the repo root

- `screenwall_generator.py`
- `streamlit_app.py`
- `requirements.txt`
- `README.md`, `CURRENT_STATE.md`, `KnownTruths` (documentation)

## Folder that belongs in the repo

- `.streamlit/secrets.toml` (optional sign-in)

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run streamlit_app.py
```

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Streamlit Community Cloud

1. Push the repo to GitHub (`main`).
2. Go to https://share.streamlit.io/
3. **New app** → repo `jkl-rgb/Screenwall-Makr`, branch `main`, main file `streamlit_app.py`.
4. Add secrets from `.streamlit/secrets.toml` if you want sign-in enabled.
5. Deploy.

## Updating the live app

- Community Cloud runs **GitHub `main`**, not your laptop. After changes: **commit + push**, wait for rebuild, or **⋮ → Reboot app**.

## Sanity check (Beta deploy)

On the app page:

1. Browser tab title: **Screenwall Makr Beta**
2. Header includes a **Beta** badge next to the title
3. Under the subtitle: **`Beta v0.1.0 · Beta — First Draft · 2026-05-15`**
4. Under “Download CSV template”, caption includes **`Release:`** with the same string and **`DXF engine:`** with `GENERATOR_ARTWORK_TAG` from `screenwall_generator.py` (e.g. `shop-RT-panelId-parallel-flange`)

If any of these are missing or old, the deployed revision is not current.

## Beta testing notes

- One **unique `panel_id`** per CSV row (duplicates overwrite in the ZIP).
- **`flange_code`** must match allowed codes exactly (`J4S`, not `J4L`).
- Large batches (100+ rows) may be slow or hit platform memory limits when zipping.
