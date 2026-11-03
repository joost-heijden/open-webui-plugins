"""Embed original UI into a standalone Open WebUI Function import."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
source = (root / 'email-composer/action.py').read_text()
html = (root / 'email-composer/card.html').read_text()
(root / 'email-composer/function.py').write_text(source.replace("'__CARD_HTML__'", repr(html)))
