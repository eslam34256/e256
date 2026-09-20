# LeakHunter AI

The maintained application is in [`leakhunter_ai/`](leakhunter_ai/README.md).

## Quick start

```bash
cd leakhunter_ai
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Run the automated checks with:

```bash
pip install -r requirements-dev.txt
pytest -q
```
