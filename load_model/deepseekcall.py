## 0922 changshengEVA: 新增deepseek的调用接口，实现直接回答与推理回答

from openai import OpenAI
client = OpenAI(api_key="sk-1e8ef4b724df474288237f4715f7064f", base_url="https://api.deepseek.com")

# Round 1
messages = [{"role": "user", "content": "你是谁？"}]
response = client.chat.completions.create(
    model="deepseek-reasoner",
    messages=messages
)

reasoning_content = response.choices[0].message.reasoning_content
content = response.choices[0].message.content

print("response:", content)

# Round 2
# messages.append({'role': 'assistant', 'content': content})
# messages.append({'role': 'user', 'content': "How many Rs are there in the word 'strawberry'?"})
# response = client.chat.completions.create(
#     model="deepseek-reasoner",
#     messages=messages
# )