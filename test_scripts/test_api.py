
import sys
import json
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()
from load_model.AlibabaEmbeddingCall import get_embed_model
embed_model = get_embed_model()
print(embed_model("我是说你说"))