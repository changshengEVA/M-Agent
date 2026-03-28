import os
from llama_index.embeddings.azure_openai import AzureOpenAIEmbedding
from llama_index.llms.azure_openai import AzureOpenAI

api_version = "2023-09-15-preview"

def get_llm(model_temperature: float) -> AzureOpenAI:
    """
    Get an instance of the AzureOpenAI GPT model
    """
    llm = AzureOpenAI(
        model="gpt-3.5-turbo-instruct",
        deployment_name="pkunj-gpt-35-turbo-instruct",
        api_key=os.getenv("AZURE_OPENAI_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_version=api_version,
        temperature=model_temperature
    )

    return llm

def get_embed_model() -> AzureOpenAIEmbedding:
    """
    Get an instance of the AzureOpenAIEmbedding model
    """
    embed_model = AzureOpenAIEmbedding(
        model='text-embedding-3-small',
        deployment_name="pkunj-text-embedding-3-small",
        api_key=os.getenv("AZURE_OPENAI_KEY"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_version=api_version,
    )
    
    return embed_model