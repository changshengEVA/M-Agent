import os
import openai

api_key = os.getenv("OPENAI_API_KEY")

def get_llm(model_temperature: float):
    """
    Get an instance of the OpenAI GPT model
    """
    def llm(prompt):
        response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=prompt,
            temperature=model_temperature,
            max_tokens=150,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0
        )
        return response.choices[0].text.strip()

    return llm

def get_embed_model():
    """
    Get an instance of the OpenAIEmbedding model
    """
    def embed_model(text):
        response = openai.Embedding.create(
            engine="text-embedding-ada-002",
            input=text
        )
        return response.data[0].embedding

    return embed_model