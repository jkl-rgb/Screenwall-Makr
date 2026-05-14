# Screenwall Makr - Streamlit deployment

## Files that belong in the repo root
- screenwall_generator.py
- streamlit_app.py
- requirements.txt

## Folder that belongs in the repo
- .streamlit/secrets.toml

## Run locally
python -m venv .venv

### Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run streamlit_app.py

### macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py

## Streamlit Community Cloud
1. Push the repo to GitHub.
2. Go to https://share.streamlit.io/
3. Click New app.
4. Select repo: jkl-rgb/Screenwall-Makr
5. Branch: main
6. Main file path: streamlit_app.py
7. Add secrets from .streamlit/secrets.toml if you want sign-in enabled.
8. Deploy.

## Updating the live app (important)

- **Community Cloud always runs code from GitHub**, not from your laptop. After editing `screenwall_generator.py` or `streamlit_app.py`, **commit and push to `main`**, then wait for the automatic rebuild or open the app on [share.streamlit.io](https://share.streamlit.io/) and use **⋮ → Reboot app**.

- **Sanity check:** On the app page, under “Download CSV template”, the caption includes **`DXF engine:`** and a tag such as **`shop-bend-face195-L027-no2Tnotch`**. That string is `GENERATOR_ARTWORK_TAG` in `screenwall_generator.py`. If it is missing or wrong, the deployed revision is not the one you think it is.
