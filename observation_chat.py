import os
from dotenv import load_dotenv
import json
from datetime import datetime
from flipflop.utils import *
from chat.utils import *
from llama_index.core.llms import ChatMessage
from llama_index.core import Settings
from llama_index.core import StorageContext, load_index_from_storage
from chat.prepare_input import find_memory,emerge_chat_prompt_wo_memory,emerge_chat_prompt_w_memory

load_dotenv()
LANGUAGE = os.getenv("LANGUAGE")

import json

def start_chat(llm, embed, memory = False, store = False, observation = None):
    """
    start to chat with the observation
    observation: the observation that input for changshengEVA who wants to talk about it.
    """
    history = ""
    displayed = ""
    turns = []  # 存储每轮对话的时间戳信息
    
    if observation is None:
        start_talk = input("\n\n\n--------------------\nZQR:")
        print("\n\n\n")
        displayed = "ZQR:" + start_talk + "\n"
        history += displayed
        # 记录第一轮用户输入的时间戳
        turns.append({
            "speaker": "ZQR",
            "text": start_talk,
            "timestamp": datetime.now().isoformat()
        })
    if not os.path.exists("./data/memory/index"):
        print_with_color("Warning: 记忆索引文件夹不存在，将使用无记忆模式", "yellow")
        memory = False
    if memory is False:
        final_prompt = emerge_chat_prompt_wo_memory(history,observation)
    else:
        Settings.llm = llm
        Settings.embed_model = embed
        storage_context = StorageContext.from_defaults(persist_dir="./data/memory/index")
        index = load_index_from_storage(storage_context)
        memory = find_memory(index, observation if observation else start_talk)
        print_with_color("Thinking:", "red")
        print(memory)
        final_prompt = emerge_chat_prompt_w_memory(history, memory, observation)
        
    # print(final_prompt)
    # raise ValueError("EVA error...")
    print_with_color("changshengEVA:","green")
    response = str(llm.chat([ChatMessage(content = final_prompt)])).split('assistant:')[-1]
    print(response)
    displayed = "changshengEVA:" + response + "\n"
    history += displayed
    # 记录第一轮AI回复的时间戳
    turns.append({
        "speaker": "changshengEVA",
        "text": response,
        "timestamp": datetime.now().isoformat()
    })
    while 1:
        print("\n\n\n--------------------\nZQR:",end="")
        message = input()
        print("\n\n\n")
        if message.lower() == "exit": break
        history += "ZQR:" + message + "\n"
        # 记录用户输入的时间戳
        turns.append({
            "speaker": "ZQR",
            "text": message,
            "timestamp": datetime.now().isoformat()
        })
        
        if memory:
            memory = find_memory(index, message)
            print_with_color("Thinking:", "red")
            print(memory)
            final_prompt = emerge_chat_prompt_w_memory(history, memory, observation)
        else:
            final_prompt = emerge_chat_prompt_wo_memory(history,observation)
        print_with_color("changshengEVA:","green")
        response = str(llm.chat([ChatMessage(content = final_prompt)])).split('assistant:')[-1]
        print(response)
        displayed = response + "\n"
        history += displayed
        # 记录AI回复的时间戳
        turns.append({
            "speaker": "changshengEVA",
            "text": response,
            "timestamp": datetime.now().isoformat()
        })
    print("OK")
    print("The following is you talked with the changshengEVA:")
    print(history)
    if store:
        store_the_history(history, llm, turns=turns, observation=observation, time=get_current_time())
        store_mem(embed, llm)


if __name__ == "__main__":
    start_chat("接收到来自好友江海共余生的QQ信息:签到了吗？")