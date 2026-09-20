# LeakHunter AI

The maintained application is in [`Saver/leakhunter_ai/`](Saver/leakhunter_ai/README.md).

## Quick start

```bash
cd Saver/leakhunter_ai
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
