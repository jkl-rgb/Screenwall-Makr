## Cursor Cloud specific instructions

### Project overview

Screenwall Makr is a single-service Python app that generates flat-pattern DXF files for perforated metal screenwall panels. No databases, Docker, or external APIs are required.

### Running the app

```
streamlit run streamlit_app.py --server.headless true --server.port 8501
```

The app serves on port 8501. Auth is disabled (`.streamlit/secrets.toml` sets `auth.required = false`).

### Key files

- `screenwall_generator.py` — core DXF generation logic, bend math, CSV parser
- `streamlit_app.py` — Streamlit web UI (file upload → DXF download)
- `screenwall_ui.py` — tkinter desktop GUI (not needed for cloud testing)
- `KnownTruths` — engineering reference for bend math and material tables
- `.cursor/rules/screenwall.mdc` — full engineering MDC reference

### Testing

There are no automated tests in the repo. To verify the generator works, create a CSV with panel specs and call `parse_csv()` + `generate_panel_dxf()` from `screenwall_generator.py`, or upload the CSV through the Streamlit UI.

The downloadable CSV template is built into the Streamlit app (click "Download CSV template").

### Gotchas

- The `assets/artform_logo.png` directory/file may not exist; the app handles this gracefully with a conditional check.
- The app uses `tempfile.TemporaryDirectory()` for DXF generation, so output files are ephemeral.
- No linter or formatter is configured in the repo. Python type checking via `pyright` or `mypy` is not set up.
