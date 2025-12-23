from huggingface_hub import snapshot_download
snapshot_download(repo_id="THUDM/chatglm3-6b", local_dir = 'F:/checkpoints/chatglm3-6b')
#snapshot_download(repo_id="BAAI/bge-large-en-v1.5", local_dir = 'F:/checkpoints/bge-large-en-v1.5')