import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# 优先使用 API_SECRET_KEY，如果不存在则使用 OPENAI_API_KEY
api_key = os.getenv("API_SECRET_KEY") or os.getenv("OPENAI_API_KEY")
base_url = os.getenv("BASE_URL") or "https://api.openai.com/v1"

def get_llm(model_temperature: float):
    """
    Get an instance of the OpenAI GPT model using OpenAI 1.x ChatCompletion API
    """
    import openai
    
    # 创建客户端
    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url
    )
    
    def llm(prompt):
        # 使用 chat completion，将 prompt 作为用户消息
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",  # 或根据 API 支持的其他模型
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=model_temperature,
            max_tokens=500,  # 增加 token 限制以容纳 JSON 输出
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0
        )
        return response.choices[0].message.content.strip()

    return llm

def get_embed_model():
    """
    Get an instance of the OpenAIEmbedding model using OpenAI 1.x API
    """
    import openai
    
    # 创建客户端
    client = openai.OpenAI(
        api_key=api_key,
        base_url=base_url
    )
    
    def embed_model(text):
        response = client.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        return response.data[0].embedding

    return embed_model