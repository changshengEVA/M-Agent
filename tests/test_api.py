
import sys
import json
import logging
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
from dotenv import load_dotenv
load_dotenv()
from m_agent.load_model.AlibabaEmbeddingCall import get_embed_model
embed_model = get_embed_model()
print(embed_model("你好"))
